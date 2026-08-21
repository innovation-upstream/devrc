"""🔴 The search-nudge state root is PER RUN — and the guard pins the SEAM, not
either side of it.

WHY THIS FILE EXISTS
--------------------
`scripts/claude-hooks/search-tool-nudge.py` kept its once-per-kind-per-session
markers under a hardcoded `$HOME/.cache/claude-search-tool-nudge` — a FIXED path
shared by every process on the box. Its own suite is a concurrency suite: it
asserts "exactly one nudge across 12 parallel invocations" for a session id it
owns, and "no test state leaks into the next run" for a prefix it cleans. Neither
claim is true of a directory a SIBLING GATE RUN is writing at the same time.

Measured 2026-08-20 with three to four full gates in flight (load 9-13), twice in
one day, same file, two different symptoms:

    concurrency: exactly one nudge across 12 parallel invocations: got 0 want 1
    no test state leaks into the next run: got ['test-search-nudge-nul-byte-…'] want []

Both surfaced NESTED inside `run-tests.sh`'s own meta-tests, so the gate's positive
controls inherited the contamination and the whole run reported FAIL — attributed
to an unrelated PR. The cost is not a flaky test; it is a FALSE RED with someone
else's name on it.

The fix has two halves and they live in different files and different languages:
the hook reads `SEARCH_TOOL_NUDGE_CACHE_DIR`, and `scripts/run-tests.sh` GUARD 9
exports a per-run temp dir into it. Either half is worthless alone, and each is
perfectly testable in isolation while the pair does nothing — the "verified in
isolation" failure `claude/RULES.md` names. So what is pinned here is the SEAM:
the runner really exports it, the hook really reads it, and the two really spell
the same name.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT (RULES.md asks for the label).
Measured by running THIS file, unchanged, against a tree whose hook, runner and
hook-suite come from 54ebf95: 7 failed, 4 passed.

  REGRESSION COVERAGE — red at 54ebf95, green here, for the right reason:

  * `test_two_runs_with_different_roots_do_not_see_each_others_state` is THE
    regression test, and it is the contamination shape made deterministic: two
    runs, one session id, two roots. At 54ebf95 the second run is deduped into
    silence, exactly as a sibling gate run silences this one.
  * `test_a_real_runner_run_lands_the_state_in_its_own_dir_not_in_HOME` covers
    the RUNNER half, driven through the real `scripts/run-tests.sh`. At 54ebf95
    the probe's state lands in the scratch `$HOME`.
  * `test_the_hooks_own_suite_writes_to_the_root_it_is_handed` covers the THIRD
    side of the seam — the suite that was going red. At 54ebf95 it writes under
    `$HOME` whatever root it is handed.
  * `test_the_runner_exports_the_name_the_hook_reads` and
    `test_the_hook_exposes_its_cache_root_variable_name` are structural, and red
    at 54ebf95 because neither side existed. Neither can see a wrong VALUE — only
    a name that stopped agreeing across the process boundary. The behavioural
    tests above are what cover the value.

  MUTATION COVERAGE, and NOT scoreable against 54ebf95:

  * `test_the_runner_refuses_when_the_hook_stops_reading_the_variable` is for the
    mutant that leaves everything looking fine — the export stays, the hook
    ignores it, and the run isolates nothing. It asserts GUARD 9's OWN message,
    not merely that the run went red, and its sibling
    `test_the_control_of_that_mutant_is_green` proves the fake-repo machinery it
    rides on can produce a PASSING run. Both are red at 54ebf95 only because the
    mutation anchor does not exist in that hook, which the helper reports as
    SKIPPED-not-scored rather than letting it read as a kill.

  INVARIANT GUARDS — green at 54ebf95, and two of them VACUOUSLY so:

  * `test_the_default_is_unchanged_when_the_variable_is_unset` is the one that
    matters operationally, and it is NOT vacuous: it is the same assertion on both
    trees, so it is the direct evidence that the unset path — the path both hosts
    actually run — did not move.
  * `test_two_runs_sharing_one_root_still_dedupe` is the control for the
    regression test. Without it "both nudged" is equally satisfied by a hook that
    lost its dedupe entirely.
  * `test_a_relative_root_is_refused` and
    `test_junk_in_the_variable_never_breaks_the_hook` pass at 54ebf95 VACUOUSLY —
    that hook reads no variable at all, so there is nothing there for a bad value
    to reach. They guard code this change introduces; they are not evidence about
    the bug.

  run:  python -m pytest scripts/tests/test_search_nudge_state_isolation.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from testlib.runner_patch import runner_with_targets, write_pytest_suite  # noqa: E402

HOOK = SCRIPTS / "claude-hooks" / "search-tool-nudge.py"
HOOK_SUITE = SCRIPTS / "claude-hooks" / "tests" / "test_search_tool_nudge.py"
RUN_TESTS = SCRIPTS / "run-tests.sh"

# 🔴 CODE ONLY. run-tests.sh is 40% commentary and GUARD 9's header quotes the
# variable name several times over — a pin read off the raw text answers a question
# about the PROSE, not about what the script does.
RUNNER_CODE = "\n".join(ln for ln in RUN_TESTS.read_text(encoding="utf-8").splitlines()
                        if not ln.lstrip().startswith("#"))

# The default cache directory name. Spelled here ON PURPOSE and not read from the
# module: this is the one place the production path is asserted rather than derived,
# so a change to `CACHE_DIR`'s default has to come here and be looked at. Its
# whole-path sibling in `claude-hooks/tests/test_on_disk_artifact_names.py` pins the
# same name from the other direction, behaviourally.
DEFAULT_LEAF = "claude-search-tool-nudge"


def _load_hook(env_overrides: dict | None = None):
    """Import the hook with a chosen environment. It resolves its cache root at
    IMPORT time into a module constant, so the environment has to be right BEFORE
    the module is executed — which is also why every caller here restores it."""
    saved = {}
    for k, v in (env_overrides or {}).items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location("_stn_seam", str(HOOK))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_HOOK_MOD = _load_hook()
# 🔴 Taken from the HOOK, so a rename on either side of the seam is visible: a pin
# that restates the name in both files goes on agreeing with itself forever. The
# literal is a FALLBACK for one case only — a revision where the hook exposes no
# such constant at all — and it exists so this file produces a per-test red/green
# matrix against an older tree instead of one collection error. It can never hide a
# rename, because `test_the_hook_exposes_its_cache_root_variable_name` fails the
# moment the fallback is what gets used.
CACHE_DIR_ENV = getattr(_HOOK_MOD, "CACHE_DIR_ENV", None) or "SEARCH_TOOL_NUDGE_CACHE_DIR"


def test_the_hook_exposes_its_cache_root_variable_name():
    """Not a skip: a check that quietly vanishes when the symbol is gone is worse
    than no check. Everything else in this file reads the name from here."""
    assert getattr(_HOOK_MOD, "CACHE_DIR_ENV", None) == CACHE_DIR_ENV, (
        "search-tool-nudge.py no longer names its cache-root variable in a "
        "constant this seam can read; the pins below are comparing a literal "
        "against itself")


_PAYLOAD = json.dumps({"tool_name": "Bash", "session_id": "seam-shared-session",
                       "tool_input": {"command": "grep -r TODO src/"}})


def _fire(home: Path, root: str | None) -> bool:
    """Run the REAL hook as a subprocess and report whether it emitted a nudge.

    `home` is a scratch directory in every call, so the operator's own
    `~/.cache/…` state is never read, written or relied upon — and at a revision
    where the override is ignored, the fallback lands there instead of in
    production.
    """
    env = dict(os.environ, HOME=str(home))
    if root is None:
        env.pop(CACHE_DIR_ENV, None)
    else:
        env[CACHE_DIR_ENV] = root
    proc = subprocess.run([sys.executable, str(HOOK)], input=_PAYLOAD,
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    return bool(proc.stdout.strip())


def _run_runner(args: list[str], env: dict | None = None,
                timeout: int = 600) -> subprocess.CompletedProcess:
    full = {**os.environ, **(env or {})}
    for k, v in list(full.items()):
        if v is None:
            del full[k]
    return subprocess.run(["bash", *args], capture_output=True, text=True,
                          timeout=timeout, cwd=str(REPO_ROOT), env=full)


def _scratch_home(tmp_path: Path) -> Path:
    home = tmp_path / "scratch-home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    return home


def _home_state(home: Path) -> Path:
    return home / ".cache" / DEFAULT_LEAF


# --------------------------------------------------------------------------- #
# REGRESSION — the contamination shape, made deterministic
# --------------------------------------------------------------------------- #
def test_two_runs_with_different_roots_do_not_see_each_others_state(tmp_path):
    """🔴 THE MEASURED DEFECT. Two runs, ONE session id, TWO roots.

    That is the concurrent-gate situation with the timing removed: a sibling run
    claims the kind first, and this run's invocation goes silent — which is
    `got 0 want 1` in the hook suite's concurrency check. At 54ebf95 the roots are
    ignored, both runs share `$HOME`, and the second is deduped: RED.
    """
    home = _scratch_home(tmp_path)
    root_a = str(tmp_path / "run-a")
    root_b = str(tmp_path / "run-b")

    first = _fire(home, root_a)
    second = _fire(home, root_b)

    assert first, "the first run did not nudge at all — the probe is wired to nothing"
    assert second, (
        "a run with its OWN state root was silenced by a DIFFERENT run's state. "
        "That is the shared-root defect: under concurrent gates this is the hook "
        "suite reporting 'exactly one nudge across 12 parallel invocations: got 0 "
        f"want 1' against an unrelated PR.\nroots: {root_a} / {root_b}")
    assert not _home_state(home).exists(), (
        f"state reached the $HOME default at {_home_state(home)} despite an "
        "explicit root — the override is not governing the write")


def test_two_runs_sharing_one_root_still_dedupe(tmp_path):
    """The control for the test above.

    Without it, "both nudged" is equally satisfied by a hook that lost its dedupe
    entirely — which would be a far worse regression than the one being fixed.
    Same session, same root: the second call MUST be silent.
    """
    home = _scratch_home(tmp_path)
    root = str(tmp_path / "one-root")

    assert _fire(home, root) is True, "the first call did not nudge"
    assert _fire(home, root) is False, (
        "a second call against the SAME root nudged again — the once-per-kind-"
        "per-session dedupe is gone, and the isolation test above proves nothing")


# --------------------------------------------------------------------------- #
# INVARIANT GUARDS — the production path, and the value the hook refuses
# --------------------------------------------------------------------------- #
def test_the_default_is_unchanged_when_the_variable_is_unset(tmp_path):
    """🔴 THE PATH BOTH HOSTS ACTUALLY RUN. Unset means exactly what it always did.

    Asserted two ways on purpose: the constant the module computes, and a real
    subprocess write landing at that path. The constant alone would pass for a
    module that computes the right string and writes somewhere else.
    """
    home = _scratch_home(tmp_path)
    mod = _load_hook({"HOME": str(home), CACHE_DIR_ENV: None})
    assert mod.CACHE_DIR == os.path.join(str(home), ".cache", DEFAULT_LEAF)
    assert mod.STATE_ROOT == os.path.join(mod.CACHE_DIR, "s")

    assert _fire(home, None) is True
    assert (_home_state(home) / "s").is_dir(), (
        "with the variable unset the hook must write under $HOME exactly as it "
        f"always has; nothing appeared at {_home_state(home) / 's'}")


def test_a_relative_root_is_refused(tmp_path):
    """A relative value falls back to the default rather than being honoured.

    A hook process inherits whatever cwd the Bash call ran in, so a relative root
    would scatter state across directories and silently break the dedupe — the
    same observable as the shared-root bug, from the opposite direction.
    """
    home = _scratch_home(tmp_path)
    mod = _load_hook({"HOME": str(home), CACHE_DIR_ENV: "some/relative/dir"})
    assert mod.CACHE_DIR == os.path.join(str(home), ".cache", DEFAULT_LEAF)


def test_junk_in_the_variable_never_breaks_the_hook(tmp_path):
    """The override is read at IMPORT time, outside main()'s try — the constraint
    `SEARCH_TOOL_NUDGE_MAX_SCAN_BYTES` already carries. An exception there exits
    non-zero with a traceback on EVERY Bash call in the session."""
    home = _scratch_home(tmp_path)
    for bad in ("", "   ", "relative/path", "~/tilde-not-expanded"):
        env = dict(os.environ, HOME=str(home))
        env[CACHE_DIR_ENV] = bad
        proc = subprocess.run([sys.executable, str(HOOK)], input=_PAYLOAD,
                              capture_output=True, text=True, env=env)
        assert (proc.returncode, "Traceback" in proc.stderr) == (0, False), (
            f"{bad!r} broke the hook:\n{proc.stderr}")


# --------------------------------------------------------------------------- #
# THE SEAM — structural
# --------------------------------------------------------------------------- #
def test_the_runner_exports_the_name_the_hook_reads():
    """One name, spelled on both sides of a process boundary.

    The name comes from the HOOK's own constant, never from a literal here: a pin
    that restates the name on both sides goes on agreeing with itself after a
    rename on either side.

    Honest scope: this sees a NAME that stopped agreeing. It cannot see a wrong
    VALUE, or an export that lands after the targets run — the behavioural tests
    below are what cover those.
    """
    assert f"export {CACHE_DIR_ENV}=" in RUNNER_CODE, (
        f"scripts/run-tests.sh no longer exports {CACHE_DIR_ENV}; every target "
        "shares the operator's real search-nudge state root with every other "
        "concurrent run on the box (GUARD 9)")
    assert CACHE_DIR_ENV in HOOK_SUITE.read_text(encoding="utf-8"), (
        f"{HOOK_SUITE.name} no longer mentions {CACHE_DIR_ENV} — it is back on a "
        "root it does not control, which is what produced the false reds")


# --------------------------------------------------------------------------- #
# THE SEAM — behavioural
# --------------------------------------------------------------------------- #
_PROBE = f'''\
import importlib.util
import json
import os

HOOK = {str(HOOK)!r}
OUT = os.environ["SEAM_PROBE_OUT"]


def test_records_where_the_hook_would_write():
    spec = importlib.util.spec_from_file_location("_stn_probe", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Drive the REAL writer, not a restatement of the layout: this is the call
    # every nudge makes, so the directory it creates is the one that collides.
    state_dir = mod._state_dir({{"session_id": "seam-probe-session"}})
    claimed = mod._claim(state_dir, "content")
    with open(OUT, "w") as fh:
        json.dump({{
            "env": os.environ.get({CACHE_DIR_ENV!r}),
            "state_root": mod.STATE_ROOT,
            "state_dir": state_dir,
            "claimed": claimed,
            "exists": os.path.isdir(state_dir),
            "home": os.environ.get("HOME"),
        }}, fh)
'''


def test_a_real_runner_run_lands_the_state_in_its_own_dir_not_in_HOME(tmp_path):
    """🔴 PLANT A REAL WRITE IN A TARGET AND FOLLOW IT.

    The two halves of the fix are exercised together, through the real
    `scripts/run-tests.sh`: the runner decides the root, the hook resolves it, a
    target writes. At 54ebf95 this run is GREEN and the directory is sitting in
    the scratch `$HOME` — which is the operator's real cache on a real run, shared
    with every concurrent gate.

    `claimed`/`exists` are the positive control. Without them "nothing under HOME"
    is equally satisfied by a probe that never wrote anything at all.
    """
    home = _scratch_home(tmp_path)
    probe_out = tmp_path / "probe.json"
    target = tmp_path / "seam_target"
    write_pytest_suite(target, 1, prefix="test_filler")
    (target / "test_probe.py").write_text(_PROBE, encoding="utf-8")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])

    proc = _run_runner([str(runner), str(REPO_ROOT)],
                       env={"HOME": str(home), "SEAM_PROBE_OUT": str(probe_out),
                            CACHE_DIR_ENV: None})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"the probe run should not fail:\n{out}"
    assert probe_out.exists(), f"the probe never ran, so it proves nothing:\n{out}"

    rec = json.loads(probe_out.read_text(encoding="utf-8"))
    assert rec["claimed"] is True and rec["exists"] is True, (
        f"the probe recorded no write, so its location proves nothing: {rec}")
    assert rec["env"], (
        f"the runner exported no {CACHE_DIR_ENV} to its targets: {rec}\n{out}")
    assert rec["state_root"].startswith(rec["env"] + os.sep), (
        "the hook did not resolve its state root inside the root the runner "
        f"exported — the export governs nothing: {rec}")
    assert not rec["state_dir"].startswith(str(home)), (
        f"the run wrote its nudge state under $HOME: {rec}")
    assert not _home_state(home).exists(), (
        f"a real runner run created {_home_state(home)} — that is the shared "
        "directory every concurrent gate on this box collides in")


def test_the_hooks_own_suite_writes_to_the_root_it_is_handed(tmp_path):
    """🔴 THE THIRD SIDE OF THE SEAM: the suite that was actually going red.

    A hook that honours the variable and a runner that exports it still leave the
    false reds in place if the SUITE keeps deriving its own root from `$HOME` —
    it would clean and inspect one directory while the hook wrote to another.
    Driven as the real script, exactly as `run-tests.sh` runs it.

    The non-empty root is the positive control: "nothing under HOME" is satisfied
    by a suite that failed before writing anything.
    """
    home = _scratch_home(tmp_path)
    root = tmp_path / "handed-root"
    env = dict(os.environ, HOME=str(home))
    env[CACHE_DIR_ENV] = str(root)
    proc = subprocess.run([sys.executable, str(HOOK_SUITE)], capture_output=True,
                          text=True, env=env, cwd=str(REPO_ROOT), timeout=900)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"the hook suite failed under an explicit root:\n{out}"
    assert (root / "s").is_dir(), (
        f"the suite wrote nothing under the root it was handed ({root}), so the "
        f"assertion below proves nothing:\n{out}")
    assert not _home_state(home).exists(), (
        f"the hook suite wrote its state to {_home_state(home)} instead of the "
        "root it was handed — this is the file that reported the false reds")


# --------------------------------------------------------------------------- #
# MUTATION — the mutant that leaves everything looking fine
# --------------------------------------------------------------------------- #
def _repo_with_mutated_hook(tmp_path: Path, old: str, new: str) -> Path:
    """A shallow symlink copy of the repo whose ONLY real file is a mutated hook.

    Cheaper and safer than copying the tree: nothing here can write into the real
    checkout, and the runner sees a `$ROOT` that behaves like the repo in every
    other respect.
    """
    fake = tmp_path / "fake-repo"
    fake.mkdir()
    for entry in REPO_ROOT.iterdir():
        # 🔴 `.git` is deliberately NOT linked. In a worktree it is a FILE holding
        # `gitdir: …`, so a link to it would give anything running with this as its
        # cwd the REAL repository's index, refs and reflog (claude/RULES.md ->
        # "any COPY you make OF it"). The runner takes ROOT as an argument and uses
        # git only to GUESS it, so nothing here needs it.
        if entry.name not in ("scripts", ".git"):
            os.symlink(entry, fake / entry.name)
    (fake / "scripts").mkdir()
    for entry in SCRIPTS.iterdir():
        if entry.name != "claude-hooks":
            os.symlink(entry, fake / "scripts" / entry.name)
    (fake / "scripts" / "claude-hooks").mkdir()
    for entry in (SCRIPTS / "claude-hooks").iterdir():
        if entry.name != HOOK.name:
            os.symlink(entry, fake / "scripts" / "claude-hooks" / entry.name)

    src = HOOK.read_text(encoding="utf-8")
    assert src.count(old) == 1, (
        f"the mutation anchor is not unique ({src.count(old)} occurrences) — this "
        "mutant was SKIPPED, not survived, and must not be scored")
    (fake / "scripts" / "claude-hooks" / HOOK.name).write_text(
        src.replace(old, new), encoding="utf-8")
    return fake


def test_the_runner_refuses_when_the_hook_stops_reading_the_variable(tmp_path):
    """🔴 THE MUTANT THAT LOOKS FINE: the export stays, the hook ignores it.

    Everything still prints a plausible path, every target still runs, and the run
    isolates nothing — which is the state this whole change exists to leave behind.
    GUARD 9's arming check is what catches it, so this asserts GUARD 9's OWN
    message and its OWN exit code rather than merely "the run went red": a run
    that failed for a different reason would be green here otherwise.
    """
    home = _scratch_home(tmp_path)
    fake = _repo_with_mutated_hook(
        tmp_path,
        "        return raw if raw and os.path.isabs(raw) else default",
        "        return default",
    )
    target = tmp_path / "mutant_target"
    write_pytest_suite(target, 1, prefix="test_filler")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])

    proc = _run_runner([str(runner), str(fake)],
                       env={"HOME": str(home), CACHE_DIR_ENV: None})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, (
        f"a hook that ignores {CACHE_DIR_ENV} produced a run with exit "
        f"{proc.returncode}; GUARD 9 must refuse to start:\n{out}")
    assert "does not resolve its state root inside" in out, (
        f"the run stopped, but not for GUARD 9's reason:\n{out}")
    assert CACHE_DIR_ENV in out, f"the failure did not name the variable:\n{out}"


def test_the_control_of_that_mutant_is_green(tmp_path):
    """The positive control for the test above: the SAME machinery, the SAME fake
    repo shape, an unmutated hook. Without it, `exit 2` could be coming from the
    symlink tree rather than from the mutation."""
    home = _scratch_home(tmp_path)
    fake = _repo_with_mutated_hook(
        tmp_path,
        "        return raw if raw and os.path.isabs(raw) else default",
        "        return raw if raw and os.path.isabs(raw) else default",
    )
    target = tmp_path / "control_target"
    write_pytest_suite(target, 1, prefix="test_filler")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])

    proc = _run_runner([str(runner), str(fake)],
                       env={"HOME": str(home), CACHE_DIR_ENV: None})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"the unmutated control run failed:\n{out}"
    assert "search-tool-nudge state isolated for this run (GUARD 9)" in out, (
        f"GUARD 9 did not report at all — the mutant test above is unreachable:\n{out}")
