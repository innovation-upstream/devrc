"""Guards for `scripts/run-tests.sh`'s PRECONDITIONS and its empty-array handling.

WHY THIS EXISTS
---------------
Two measured holes, both of which let the runner produce output that reads
reassuring (or merely confusing) rather than naming what is wrong.

1. ``REQUIRED_TOOLS`` could not express its own most important precondition.
   It is a list of BINARIES checked with ``command -v``. pytest is not a binary
   this runner calls -- it is a MODULE (``python -m pytest``) -- so the one
   guard whose whole job is "the thing that runs the tests is present" was
   structurally unable to check pytest. It also asserted ``python3`` while the
   runner actually invokes ``python``.

   MEASURED 2026-08-03 with every REQUIRED_TOOLS binary present but no pytest
   importable: all 17 targets printed ``could not parse pytest's summary`` and
   the run ended ``TOTAL collected=0 … RESULT: FAIL``, exit 1.

   Being precise, because it changes how much this matters: the gate did NOT go
   green. GUARD 4 and GUARD 3 both fired. The defect is DIAGNOSTIC -- seventeen
   copies of a message blaming pytest's OUTPUT FORMAT for what was actually a
   missing dependency, which is the #276 shape (a real finding that reads like
   an environment fault).

2. ``declare -a RESULTS`` / ``declare -a SKIP_LINES`` leave the arrays DECLARED
   BUT UNSET. Under ``set -u`` the first ``${#arr[@]}`` on a still-empty array
   aborts the command with ``unbound variable`` (measured on bash 5.3.15).
   With zero skips this printed a raw
   ``run-tests.sh: line 479: SKIP_LINES: unbound variable`` where the skip list
   belonged, and the unpinned-skip loop below it never ran. No ``set -e``, so
   the script continued and the skip-TOTAL accounting still fired -- the damage
   was confined to the diagnostic path, at exactly the moment someone is trying
   to read why the gate is red.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT
------------------------------------------------
Per claude/RULES.md:

  * ``test_missing_pytest_module_is_named``, ``test_python_is_a_required_tool``
    and ``test_empty_arrays_are_initialised_assigned`` are REGRESSION coverage.
    Each is red at origin/main for its own reason (see the PR matrix).

  * ``test_an_empty_target_directory_is_loud`` and
    ``test_a_typod_target_entry_is_loud`` are REACHABILITY proofs for guards
    that already existed before this PR. They are NOT regression coverage for
    this change -- they were requested as known-bad-state validation, and they
    pass at origin/main too. Their value is that nothing had ever proven those
    two paths could actually fire.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from testlib.mockbin import write_exec  # noqa: E402
from testlib.runner_patch import runner_with_targets  # noqa: E402
RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"


def _run(args: list[str], env: dict | None = None, timeout: int = 300):
    return subprocess.run(
        ["bash", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _runner_with_targets(tmp_path: Path, targets: list[str]) -> Path:
    """Copy run-tests.sh with HERMETIC_TARGETS replaced wholesale.

    Replacing the whole block (rather than injecting one entry) keeps these
    tests FAST: the copy runs only the target under test instead of the full
    ~7,200-test suite, which is what makes a per-target reachability proof
    affordable at all.

    Delegates to `testlib.runner_patch` -- it rewrites TARGET_FLOORS in the same
    pass, which a copy MUST do or it dies at the floor pin before reaching the
    guard under test.
    """
    return runner_with_targets(tmp_path, targets)


# --------------------------------------------------------------------------
# Guard the guard.
# --------------------------------------------------------------------------

def test_the_runner_copy_helper_actually_works(tmp_path):
    """POSITIVE CONTROL for _runner_with_targets.

    Every mutation test below rests on this rewrite landing. If the regex
    stopped matching, the copies would silently be the UNMODIFIED runner and the
    'known-bad state' tests would be exercising the real target list instead --
    passing or failing for reasons that have nothing to do with the case they
    claim to cover. So prove the rewrite both applies and takes effect.
    """
    runner = _runner_with_targets(tmp_path, ["scripts/tests"])
    text = runner.read_text()
    assert "scripts/dl-router/tests" not in text.split("HERMETIC_TARGETS=(")[1].split("\n)")[0]
    proc = _run([str(runner), "--check-targets", str(REPO_ROOT)])
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "all 1 hermetic target(s) resolve" in proc.stdout, (
        f"the copied runner did not use the rewritten 1-entry list.\n{proc.stdout}"
    )


# --------------------------------------------------------------------------
# REGRESSION: hole 2 -- the pytest module precondition.
# --------------------------------------------------------------------------

def test_python_is_a_required_tool():
    """The runner invokes `python -m pytest`; it must assert `python`.

    origin/main listed `python3` -- a binary this script never calls.
    """
    src = RUN_TESTS.read_text()
    m = re.search(r"^REQUIRED_TOOLS=\((.*?)\)", src, re.M | re.S)
    assert m, "could not find REQUIRED_TOOLS in run-tests.sh"
    tools = m.group(1).split()
    assert "python" in tools, (
        f"REQUIRED_TOOLS does not include `python`, but the runner calls "
        f"`python -m pytest`. Got: {tools}"
    )


def test_every_required_tool_is_reachable_by_the_prepush_tier():
    """🔴 THE SEAM: two tiers satisfy ONE list by DIFFERENT means.

    `run-tests.sh` OWNS `REQUIRED_TOOLS`. `flake.nix` satisfies it with
    `checks.pytests.nativeBuildInputs`; `githooks/tests-on-push.sh` satisfies it
    with a nix-shell for python PLUS the ambient PATH. Both were verified alone;
    the RELATIONSHIP was not — so an entry could be added, declared in the flake,
    and be unsatisfiable in the other tier forever.

    That happened. `logrotate` reaches the claude-log-rotate systemd unit through
    `makeBinPath` in nix/home.nix and is in NO `home.packages`, so it is on no
    interactive PATH on either host. MEASURED 2026-08-11 on the workbench: the
    dev-host tier died at GUARD 1 — "required tool(s) missing from PATH:
    logrotate", exit 2, ZERO tests collected.

    So: every REQUIRED_TOOLS entry must be either a binary the pre-push hook
    DECLARES in its own nix-shell, or one that is genuinely present on this host's
    PATH right now. Nothing may be required on faith.
    """
    tools = re.search(r"^REQUIRED_TOOLS=\((.*?)\)", RUN_TESTS.read_text(),
                      re.M | re.S)
    assert tools, "could not find REQUIRED_TOOLS in run-tests.sh"
    required = tools.group(1).split()
    assert required, "REQUIRED_TOOLS parsed as EMPTY — the regex stopped matching"

    hook = (REPO_ROOT / "githooks" / "tests-on-push.sh").read_text()
    code = [ln for ln in hook.splitlines() if not ln.lstrip().startswith("#")]

    # 🔴 A DECLARATION IS NOT A CALL SITE. Reading `-p <pkg>` from anywhere in the
    # file counts what the hook *mentions*; what matters is what the nix-shell it
    # actually RUNS brings in. A first draft of this test read the declaration
    # only, and stayed GREEN under a mutation that left `TOOL_ENV=` in place while
    # reverting both invocations to the old python-only env — passing while the
    # exact measured outage was fully reintroduced. So pin the wiring first.
    # 🔴 The hook moved from `nix-shell -p …` to `nix develop` (2026-08-22). The
    # WIRING pin below is unchanged in spirit and had to change in form. The
    # reason for the move is measured and is the whole point of this seam:
    # `nix-shell --run` executes the user's shell hooks, which activate a venv
    # belonging to whatever CWD you are standing in, so the pinned env was not
    # pinned at all. From a venv-owning cwd:
    #     nix-shell -p "python312.withPackages(…)" --run python -> …/that-repo/.venv/bin/python
    #     nix develop <repo> --command python                   -> /nix/store/…-env/bin/python
    # (Setting VIRTUAL_ENV by hand from a NEUTRAL cwd reproduces nothing — both
    # forms then give the store python. The trigger is the cwd, not the variable.)
    invocations = [ln for ln in code if "nix develop" in ln and "--command" in ln]
    assert len(invocations) == 2, (
        f"expected 2 `nix develop … --command` invocations in the hook (the env "
        f"probe and the suite run); found {len(invocations)}:\n"
        + "\n".join(f"  {ln.strip()}" for ln in invocations)
    )
    # And the vulnerable form must not come back.
    revived = [ln for ln in code if "nix-shell" in ln and "--run" in ln]
    assert not revived, (
        "the hook is using `nix-shell … --run` again — that form inherits a venv "
        "from the caller's cwd, which is devrc#698:\n"
        + "\n".join(f"  {ln.strip()}" for ln in revived)
    )
    # Each invocation must target THIS repo's devShell — `nix develop` with some
    # other flake ref would be the same class of drift the TOOL_ENV pin guarded.
    for ln in invocations:
        assert '"$REPO_ROOT"' in ln, (
            f"this `nix develop` does not target $REPO_ROOT, so it does not get "
            f"the repo's own devShell: {ln.strip()}"
        )

    # The declared set now comes from flake.nix's `gateTools` — the ONE list the
    # devShell and checks.pytests share — instead of a TOOL_ENV local to the
    # hook. Same question ("can the pre-push tier supply this?"), single source.
    flake_txt = (REPO_ROOT / "flake.nix").read_text()
    gate_m = re.search(r"gateTools\s*=\s*\[(.*?)\]", flake_txt, re.S)
    assert gate_m, "could not find gateTools in flake.nix — this check is reading nothing"
    declared = {
        tok.split(".")[-1]
        for tok in gate_m.group(1).split()
        if tok.startswith("pkgs.")
    }
    assert declared, "gateTools parsed as EMPTY — the regex stopped matching"

    # 🔴 `gatePyEnv` is a let-binding, NOT a `pkgs.` token, so the comprehension
    # above drops it — and `python`/`python3` can never be missing from
    # `shutil.which` on the test process's own PATH, so deleting gatePyEnv from
    # gateTools left this test GREEN. Measured. The old TOOL_ENV form compensated
    # with an explicit `declared |= {"python","python3"}`; the rewrite lost it.
    # Assert the binding is IN the list rather than re-adding the names blindly,
    # so the pin fails when the interpreter really is dropped.
    assert "gatePyEnv" in gate_m.group(1), (
        "flake.nix gateTools no longer includes gatePyEnv — the devShell and the "
        "pre-push tier would have no pinned interpreter, and `shutil.which` "
        "cannot notice because the test process always has some python."
    )
    declared |= {"python", "python3"}

    # 🔴 `shutil.which` reads THIS process's PATH, and since the
    # no-real-launchers fixture landed (scripts/tests/conftest.py) that PATH
    # begins with a directory of 12 stubs. None of them is a REQUIRED_TOOLS
    # entry today, so this check is unaffected — and it stays that way because
    # test_no_real_launchers.py::test_no_required_tool_is_satisfied_by_a_stub
    # asserts the two sets are disjoint. If that ever fails, THIS test is the
    # one that would have gone quietly green on a stub.
    unsatisfied = [
        t for t in required
        if t not in declared and shutil.which(t) is None
    ]
    assert not unsatisfied, (
        f"REQUIRED_TOOLS entries the pre-push tier cannot supply: "
        f"{unsatisfied}.\n"
        f"  run-tests.sh aborts at GUARD 1 with exit 2 and runs ZERO tests, so "
        f"the gate silently stops being a gate.\n"
        f"  Fix by adding the package to `gateTools` in flake.nix — ONE list, "
        f"shared by devShells.default (which the pre-push hook runs via "
        f"`nix develop`) and checks.pytests. Do NOT drop the entry from "
        f"REQUIRED_TOOLS.\n"
        f"  gateTools-declared={sorted(declared)}"
    )


def test_logrotate_is_declared_by_the_prepush_hook_specifically():
    """The narrow pin for the measured case, so it cannot silently regress.

    Kept separate from the seam test above because that one would go green again
    the moment anything put logrotate on the ambient PATH — which is not the fix,
    and would make the tier depend on a host's incidental state again.
    """
    # 🔴 STRONGER THAN THE OLD FORM, not weaker. The hook used to declare
    # logrotate in its OWN `nix-shell -p` list, independently of flake.nix — two
    # declarations of one requirement, which the previous comment admitted
    # "nothing cross-checks". Since 2026-08-22 the hook runs `nix develop`, so
    # both tiers draw from the SINGLE `gateTools` list: `devShells.default`
    # (packages = gateTools) and `checks.pytests` share it verbatim. Pin that
    # shared list, so the two tiers cannot drift apart by construction.
    flake = (REPO_ROOT / "flake.nix").read_text()
    gate = re.search(r"gateTools\s*=\s*\[(.*?)\]", flake, re.S)
    assert gate, "could not find gateTools in flake.nix — this pin is not reading the list"
    assert "logrotate" in gate.group(1), (
        "flake.nix gateTools must supply logrotate: it is in run-tests.sh "
        "REQUIRED_TOOLS and on no host's interactive PATH (nix/home.nix gives it "
        "only to the claude-log-rotate unit's makeBinPath). MEASURED 2026-08-11: "
        "without it the pre-push tier died at GUARD 1 with ZERO tests run."
    )
    assert re.search(r"devShells\.\$\{system\}\.default[^}]*?packages\s*=\s*gateTools", flake, re.S), (
        "devShells.default no longer uses gateTools, so the pre-push tier (which "
        "runs `nix develop`) and checks.pytests can drift apart again."
    )


def test_missing_pytest_module_is_named(tmp_path):
    """RED at origin/main. A pytest-less environment must fail with ONE named error.

    Drives the real runner under a PATH whose `python` cannot import pytest, and
    asserts the failure names the MODULE -- not pytest's output format. The
    negative assertion is the point: at origin/main this run produced 17 copies
    of 'could not parse pytest's summary', a diagnosis pointing at the wrong
    subsystem entirely.
    """
    # A `python` shim that exists and runs but has no pytest.
    #
    # 🔴 Written via testlib.mockbin.write_exec, NOT by hand. The first draft
    # wrote its own `#!{shutil.which("bash")}` shebang and the repo-wide runtime
    # -shebang scanner (scripts/tests/test_runtime_shebangs.py, added by #306)
    # failed it IN THE SANDBOX ONLY — the dev-host run of this file alone was
    # green, because the scanner lives in a different file. Exactly the two-tier
    # lesson: a per-file dev-host run cannot see a repo-wide guard.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    write_exec(
        bindir / "python",
        'if [ "$1" = "-m" ] && [ "$2" = "pytest" ]; then\n'
        '  echo "python: No module named pytest" >&2; exit 1\n'
        "fi\n"
        "exit 0\n",
    )

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"

    proc = _run([str(RUN_TESTS), str(REPO_ROOT)], env=env)
    out = proc.stdout + proc.stderr

    assert proc.returncode == 3, (
        f"expected the ENVIRONMENT precondition to abort with exit 3, got "
        f"{proc.returncode}. 3 is what githooks/tests-on-push.sh degrades on; 2 "
        f"means a repo-content guard and BLOCKS the push.\n{out}"
    )
    assert "python -m pytest` is not runnable" in out, (
        f"the failure did not name the pytest MODULE precondition.\n{out}"
    )
    # THIS guard's own reason, not a neighbour's: the old, misleading message
    # must be gone, and no suite may have been attempted.
    assert "could not parse pytest's summary" not in out, (
        "the runner still blames pytest's OUTPUT FORMAT for a missing "
        f"dependency.\n{out}"
    )
    assert "=== pytest " not in proc.stdout, (
        f"the runner started a suite despite the precondition failing.\n{proc.stdout}"
    )





def test_guard1c_dep_list_matches_the_flake_interpreter():
    """🔴 GUARD 1c's dep tuple and flake.nix's `gatePyEnv` are two hand-maintained
    copies of one list. They agree today and nothing made them.

    The failure modes are ASYMMETRIC, which is why this needs a gate rather than
    care: forget to add a new import to GUARD 1c and it goes SILENT (the guard
    stops covering the dep it exists to check); forget `gatePyEnv` and the guard
    refuses EVERY run. One is invisible, the other is loud — so the invisible one
    is the one a test has to catch.

    This is the same drift class the round eliminated for `gateTools`, one level
    down: the round replaced the hook's private TOOL_ENV with the shared list, and
    left this pair unshared.
    """
    runner = RUN_TESTS.read_text()
    m = re.search(r'need = \((.*?)\)', runner, re.S)
    assert m, "could not find GUARD 1c's `need` tuple — this pin is reading nothing"
    guard_deps = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', m.group(1)))
    assert guard_deps, "GUARD 1c's dep tuple parsed as EMPTY"

    flake = (REPO_ROOT / "flake.nix").read_text()
    fm = re.search(r"gatePyEnv\s*=\s*pkgs\.python312\.withPackages\s*\(ps:\s*with ps;\s*\[(.*?)\]",
                   flake, re.S)
    assert fm, "could not find gatePyEnv in flake.nix"
    flake_deps = set(fm.group(1).split())
    assert flake_deps, "gatePyEnv parsed as EMPTY"

    # 🔴 EVERY NAME GUARD 1c CHECKS MUST ACTUALLY IMPORT. A mapping table alone is
    # the wrong instrument: it needs an entry per nixpkgs-attr/module mismatch and
    # says nothing when one is missing. Measured — adding `pillow` to BOTH lists
    # (the natural fix when the cross-check fires) passed this test, while the
    # runner then failed with `missing : pillow`, because nixpkgs `pillow` imports
    # as `PIL`. That is rc 3, which the pre-push hook DEGRADES on: a silent,
    # permanent gate-off with a fully green suite. Same shape for
    # beautifulsoup4→bs4, attrs→attr, protobuf→google.protobuf.
    #
    # The interpreter running this test IS gatePyEnv (under the devShell and under
    # the flake check), so importability is checkable here directly, and it needs
    # no table.
    import importlib
    unimportable = []
    for dep in sorted(guard_deps):
        try:
            importlib.import_module(dep)
        except Exception as exc:
            unimportable.append(f"{dep} ({type(exc).__name__})")
    assert not unimportable, (
        f"GUARD 1c checks names that do not import from the gate interpreter: "
        f"{unimportable}. The runner would exit 3 on every run, the hook would "
        f"DEGRADE, and the only automatic gate would be silently off. Use the "
        f"MODULE name, not the nixpkgs attribute (pyyaml→yaml, pillow→PIL)."
    )

    # The nixpkgs attribute is `pyyaml`; the importable module is `yaml`. The
    # table only has to cover attrs actually present in gatePyEnv — the import
    # assertion above is what catches an unmapped one.
    NIX_TO_MODULE = {"pyyaml": "yaml"}
    flake_modules = {NIX_TO_MODULE.get(d, d) for d in flake_deps}

    missing_from_guard = flake_modules - guard_deps
    assert not missing_from_guard, (
        f"flake.nix gatePyEnv supplies {sorted(missing_from_guard)} but GUARD 1c "
        f"does not check them — the guard would pass an interpreter lacking a dep "
        f"the suites import. This is the SILENT direction."
    )
    missing_from_flake = guard_deps - flake_modules
    assert not missing_from_flake, (
        f"GUARD 1c requires {sorted(missing_from_flake)} but gatePyEnv does not "
        f"supply them — the guard would refuse every run."
    )

def test_the_hook_degrades_on_ENV_faults_and_BLOCKS_on_repo_content():
    """🔴 The exit-code split, and the reason it exists.

    A first version of the pre-push hook degraded on rc 2. `run-tests.sh` has NINE
    `exit 2` sites and only four are environmental; the rest are REPO-CONTENT
    guards -- the target list, the floor table, the launcher stubs, the spool
    wiring -- whose own messages warn "do NOT delete the entry to make this pass
    -- that is how a suite stops running while the gate goes green". Degrading on
    2 produced precisely that, on the ONLY tier that runs automatically: this repo
    has no CI and no branch protection (pinned by test_ci_claim_matches_reality).

    So the runner now exits 3 for environment faults, and this asserts BOTH sides
    -- the hook degrading on 3 is worthless if it also degrades on 2.

    The BEHAVIOURAL half is already covered: `test_a_typod_target_is_named`
    below drives the real runner with a bogus target and asserts exit 2. This
    test pins the WIRING that turns that 2 into a blocked push; a first draft
    of it duplicated the behavioural half as a hollow assertion that a
    directory exists, which would have read as coverage while providing none.
    """
    runner = RUN_TESTS.read_text()
    hook = (REPO_ROOT / "githooks" / "tests-on-push.sh").read_text()
    hook_code = [ln for ln in hook.splitlines() if not ln.lstrip().startswith("#")]

    # The runner must keep BOTH codes in play; collapsing to one loses the split.
    assert "exit 3" in runner, "no environment guard exits 3 — the split is gone"
    assert [ln for ln in runner.splitlines() if ln.strip() == "exit 2"], (
        "no guard exits 2 any more — repo-content failures have become degradable"
    )

    # The hook degrades on 3 ...
    deg = [ln for ln in hook_code if "run_rc" in ln and '-eq 3' in ln]
    assert deg, "the hook no longer branches on rc 3, so an env fault blocks again"
    # ... and must NOT degrade on 2.
    bad = [ln for ln in hook_code if "run_rc" in ln and '-eq 2' in ln]
    assert not bad, (
        "the hook branches on rc 2 — repo-content guards would be degraded away:\n"
        + "\n".join(f"  {ln.strip()}" for ln in bad)
    )


def test_missing_suite_dependencies_are_named(tmp_path):
    """GUARD 1c. pytest importable is NECESSARY, not sufficient.

    MEASURED 2026-08-21: running the suite with a cwd inside an UNRELATED repo
    resolved `python` to that repo's `.venv`. pytest imported, so GUARD 1b passed,
    and the run produced

        FAIL scripts/mail-actions/tests   (collected 0 tests)
        FAIL scripts/signal/tests         (collected=2 below floor 553, errors=2)
        FAIL scripts/initiatives/tests    (collected=784 ... failed=9)

    13 failures, every one an artifact of deps missing from that interpreter. The
    only tell was a traceback path naming the other repo's site-packages.

    🔴 THE PROPERTY, NOT A PROXY. The first version of this guard refused a
    VIRTUALENV (`sys.prefix != sys.base_prefix`) and was wrong in BOTH directions,
    measured: a nix `withPackages` env missing psycopg2+minio -- the exact shape
    above -- PASSED it silently, while a complete `venv --system-site-packages`
    was REFUSED. This shim reproduces the under-inclusive case: a `python` that
    HAS pytest and lacks two suite deps, which the venv proxy could never see.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # pytest present (GUARD 1b passes) -- but the dependency probe reports two
    # modules missing, which is the state the venv proxy scored as fine.
    write_exec(
        bindir / "python",
        'if [ "$1" = "-m" ] && [ "$2" = "pytest" ]; then\n'
        '  echo "pytest 8.0.0"; exit 0\n'
        "fi\n"
        'if [ "$1" = "-c" ]; then\n'
        '  echo "DEPS:psycopg2,minio"\n'
        '  echo "EXE:/tmp/shimmed/python"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"

    proc = _run([str(RUN_TESTS), str(REPO_ROOT)], env=env)
    out = proc.stdout + proc.stderr

    assert proc.returncode == 3, (
        f"expected the ENVIRONMENT precondition to abort with exit 3, got "
        f"{proc.returncode}.\n{out}"
    )
    # THIS guard's own reason, and the NAMES -- "something is wrong with your
    # environment" is the diagnosis that cost four attempts to refine.
    assert "missing suite dependencies" in out, (
        f"the failure did not name the dependency precondition.\n{out}"
    )
    assert "psycopg2" in out and "minio" in out, (
        f"the failure did not name WHICH modules are missing.\n{out}"
    )
    # The interpreter must be reported -- a green whose environment you cannot
    # see is what made the original failure expensive.
    assert "run-tests: interpreter " in out, (
        f"the resolved interpreter was not reported.\n{out}"
    )
    assert "=== pytest " not in proc.stdout, (
        f"the runner started a suite despite the precondition failing.\n{proc.stdout}"
    )


def test_dependency_probe_fails_CLOSED_when_unreadable(tmp_path):
    """An unreadable probe must REFUSE, never pass silently.

    Anything the interpreter writes to stdout ahead of the probe (a
    `sitecustomize.py` on PYTHONPATH will do it) can shift a positional parse.
    The first version read lines 1 and 2 with `sed -n 1p/2p`; under stdout noise
    it passed the guard AND printed a confidently wrong interpreter path. This
    shim emits no probe lines at all, which is the same class.

    GUARD 4 already fails the run when pytest's summary is unparseable; a
    precondition that cannot be read is not a satisfied precondition.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    write_exec(
        bindir / "python",
        'if [ "$1" = "-m" ] && [ "$2" = "pytest" ]; then\n'
        '  echo "pytest 8.0.0"; exit 0\n'
        "fi\n"
        'if [ "$1" = "-c" ]; then\n'
        '  echo "unrelated chatter on stdout"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )

    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"

    proc = _run([str(RUN_TESTS), str(REPO_ROOT)], env=env)
    out = proc.stdout + proc.stderr

    assert proc.returncode == 3, (
        f"an unreadable probe must abort with exit 3 (environment), got "
        f"{proc.returncode}.\n{out}"
    )
    assert "could not read the interpreter dependency probe" in out, (
        f"the failure did not name the unreadable probe.\n{out}"
    )
    assert "=== pytest " not in proc.stdout, (
        f"the runner started a suite despite an unreadable precondition.\n{proc.stdout}"
    )

# --------------------------------------------------------------------------
# REGRESSION: hole 3 -- set -u vs. an empty array.
# --------------------------------------------------------------------------

def test_empty_arrays_are_initialised_assigned():
    """RED at origin/main. `declare -a foo` is unset under `set -u`.

    Pinned as source structure rather than behaviour because reproducing it
    needs a full zero-skip run of the real suite. The mechanism is exact and
    measured (bash 5.3.15): `declare -a A; echo ${#A[@]}` under `set -u` aborts
    with 'A: unbound variable', while `A=()` does not.
    """
    src = RUN_TESTS.read_text()
    offenders = re.findall(r"^\s*declare -a (\w+)\s*$", src, re.M)
    assert not offenders, (
        f"{offenders} are declared with bare `declare -a`, which leaves them "
        "UNSET under `set -u`; the first ${#arr[@]} on a still-empty array "
        "aborts with 'unbound variable'. Use `NAME=()` instead."
    )
    for name in ("RESULTS", "SKIP_LINES"):
        assert re.search(rf"^{name}=\(\)\s*$", src, re.M), (
            f"{name} is not initialised with an explicit `{name}=()`"
        )


def test_set_u_empty_array_mechanism_holds():
    """POSITIVE CONTROL for the test above.

    The source-structure pin is only meaningful if the bash behaviour it
    describes is real on the bash actually running the gate. Assert BOTH halves
    -- `declare -a` fails, `=()` succeeds -- so this cannot pass on a bash where
    neither form errors and the pin above would be guarding nothing.
    """
    bad = subprocess.run(
        ["bash", "-c", 'set -uo pipefail; declare -a A; echo "${#A[@]}"'],
        capture_output=True, text=True,
    )
    good = subprocess.run(
        ["bash", "-c", 'set -uo pipefail; A=(); echo "${#A[@]}"'],
        capture_output=True, text=True,
    )
    assert bad.returncode != 0 and "unbound variable" in bad.stderr, (
        "`declare -a A` + `${#A[@]}` did NOT error under set -u on this bash; "
        f"the pin above may be guarding a non-issue here.\n{bad.stderr}"
    )
    assert good.returncode == 0 and good.stdout.strip() == "0", (
        f"`A=()` did not behave as the fix requires.\n{good.stderr}"
    )


# --------------------------------------------------------------------------
# REACHABILITY for pre-existing guards (NOT regression coverage for this PR).
# --------------------------------------------------------------------------

def test_an_empty_target_directory_is_loud(tmp_path):
    """A target that EXISTS but holds no tests must fail, not pass.

    GUARD 5 accepts it (the directory is real), so the only thing standing
    between an emptied suite and a green gate is the per-directory
    ``collected < 1`` floor. Nothing had ever proven that path could fire.
    """
    empty = tmp_path / "empty_tests"
    empty.mkdir()
    runner = _runner_with_targets(tmp_path, [str(empty)])
    proc = _run([str(runner), str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"an EMPTY target directory produced a passing run.\n{out}"
    )
    assert "collected 0 tests" in out, (
        f"the run failed but not for the empty-directory reason.\n{out}"
    )
    assert "RESULT: FAIL" in proc.stdout, out


def test_a_typod_target_entry_is_loud(tmp_path):
    """A misspelled target must be named, not silently dropped."""
    typo = "scripts/dl-rooter/tests"
    runner = _runner_with_targets(tmp_path, [typo])
    proc = _run([str(runner), "--check-targets", str(REPO_ROOT)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, f"a typo'd target did not abort GUARD 5.\n{out}"
    assert typo in out, f"GUARD 5 failed but never named {typo!r}.\n{out}"
    assert "does not exist" in out, f"GUARD 5 named {typo!r} but not why.\n{out}"


def test_a_failing_run_never_exits_zero(tmp_path):
    """`RESULT: FAIL` and a zero exit must be impossible.

    The report that motivated this PR said a bare worktree run printed
    ``RESULT: FAIL`` and exited 0. That did NOT reproduce (measured: exit 1),
    and the structure forbids it -- ``RESULT: FAIL`` is printed only when
    ``fail != 0`` and the very next statement is ``exit "$fail"``. The most
    likely origin is reading the status through a pipeline (``| tail``), which
    yields the LAST command's status, not the runner's.

    This pins the invariant end-to-end anyway, against a run forced red by the
    empty-directory case, so the claim can never quietly become true.
    """
    empty = tmp_path / "empty_tests"
    empty.mkdir()
    runner = _runner_with_targets(tmp_path, [str(empty)])
    proc = _run([str(runner), str(REPO_ROOT)])
    if "RESULT: FAIL" in proc.stdout:
        assert proc.returncode != 0, (
            "the runner printed RESULT: FAIL and exited 0 — a reassuring exit "
            f"code over a failed run.\nexit={proc.returncode}\n{proc.stdout}"
        )
    else:
        pytest.fail(
            "could not force a RESULT: FAIL, so this invariant was never "
            f"exercised — the test would pass vacuously.\n{proc.stdout}"
        )


@pytest.mark.parametrize("rc,should_block", [
    (0, False),   # suite passed
    (1, True),    # tests ran and failed
    (2, True),    # a REPO-CONTENT guard refused — blocks despite zero tests
    (3, False),   # an ENVIRONMENT precondition — degrades
])
def test_the_hook_ACTUALLY_blocks_or_degrades_per_exit_code(tmp_path, rc, should_block):
    """🔴 The behavioural half. Its sibling above reads the hook's SOURCE and
    asserts it branches on 3 and not on 2 — a sound structural pin, but it does
    not assert the branch reaches `degrade`, and nothing else in this repo ever
    EXECUTES `githooks/tests-on-push.sh`; it is only ever `read_text()`.

    So this drives the real hook against a stub runner at each code. The matrix is
    the whole contract of the 2026-08-22 exit-code split.

    🔴 THE POSITIVE CONTROL BELOW IS NOT OPTIONAL. A first version of this test
    built a bare tmp repo; the hook's own self-detection (`flake.nix` must exist
    and contain DEVRC) made it no-op and return 0, so the two ALLOW rows passed
    VACUOUSLY while only the BLOCK rows failed. A matrix half of which cannot fail
    is worse than no matrix. `stub runner` in the output is the proof the hook
    actually reached the runner.
    """
    repo = tmp_path / "devrc"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".git").mkdir()
    write_exec(repo / "scripts" / "run-tests.sh", f'echo "stub runner"; exit {rc}\n')
    # Self-detection: flake.nix present AND mentioning DEVRC.
    (repo / "flake.nix").write_text('{ description = "DEVRC stub"; }\n')

    # `nix` shim: run whatever follows --command, so the hook's own control flow
    # is what is under test rather than a real devShell build.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    write_exec(
        bindir / "nix",
        'shift_to_command() {\n'
        '  while [ "$#" -gt 0 ]; do\n'
        '    if [ "$1" = "--command" ]; then shift; exec "$@"; fi\n'
        '    shift\n'
        '  done\n'
        '  exit 0\n'
        '}\n'
        'shift_to_command "$@"\n',
    )

    hook = repo / "tests-on-push.sh"
    hook.write_text((REPO_ROOT / "githooks" / "tests-on-push.sh").read_text())
    hook.chmod(0o755)

    env = dict(os.environ)
    env["TESTS_ON_PUSH"] = "on"
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(["bash", str(hook), str(repo)], cwd=str(repo), env=env,
                          capture_output=True, text=True, input="")
    out = proc.stdout + proc.stderr

    # POSITIVE CONTROL — without this the ALLOW rows pass on a no-op.
    assert "stub runner" in out, (
        f"the hook never reached the runner, so this row proves nothing "
        f"(rc={rc}, exit={proc.returncode}).\n{out}"
    )

    if should_block:
        assert proc.returncode != 0, (
            f"rc={rc} must BLOCK the push, but the hook exited 0.\n{out}"
        )
    else:
        assert proc.returncode == 0, (
            f"rc={rc} must ALLOW the push (pass or degrade), got "
            f"{proc.returncode}.\n{out}"
        )
