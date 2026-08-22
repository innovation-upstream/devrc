"""GUARD 9's pins: no test may mutate a REAL repository, and the proof it can't.

🔴 WHAT HAPPENED (2026-08-21, the operator's own workbench)
------------------------------------------------------------
Running the Python test tier set `core.bare = true` on the working clone at
`~/workspace/devrc`, renamed its `main` to `trunk`, repointed its `origin` at a
pytest tmpdir, left ~50 fixture commits in it, and pushed ~40 of them over
`refs/heads/main` on the PUBLIC repo in 43 seconds. `trunk` and three feature
branches were hit too.

🔴 TWO TRACED MECHANISMS WERE REFUTED. THEY ARE PINNED HERE SO NOBODY RE-DERIVES
--------------------------------------------------------------------------------
  1. "drift-check.sh's REMOTE leg pipes the payload through a STUB `ssh` that
     runs it LOCALLY against `$HOME/workspace/devrc`." FALSE: the stub drains
     stdin and exits, so the payload is never executed. Pinned by
     `test_the_drift_check_ssh_stub_never_executes_its_payload`.
  2. "git exports GIT_DIR into the pre-push hook, so `githooks/tests-on-push.sh`
     runs the suite with it set." FALSE, measured on git 2.55: a pre-push hook
     gets GIT_EDITOR, GIT_EXEC_PATH and GIT_PREFIX, and no GIT_DIR. Pinned by
     `test_a_pre_push_hook_does_not_inherit_git_dir`.

🔴 THE MECHANISM THAT DOES REPRODUCE IT
-----------------------------------------
An ambient **GIT_DIR overrides `git -C <path>`**. Every fixture in this tree
binds `-C` or `cwd=` correctly; one inherited variable retargets all of them at
once and no call site looks wrong.
`test_an_ambient_git_dir_reproduces_the_incident_signature` replays
`scripts/repo-cos/tests/test_prescan.py::_init_clone` verbatim against a
tmp_path fixture with GIT_DIR naming a victim, and asserts the victim's branch
is renamed, its identity rewritten, the fixture commit landed in it, and the
commit PUSHED to the victim's own remote -- the incident, byte for byte.

🔴 WHY THE FIX IS NOT "AUDIT THE CALL SITES" AND NOT A CLEANUP STEP
--------------------------------------------------------------------
Auditing found nothing because there is nothing at any call site to find. And a
teardown that restores the repo cannot help: a killed run, an assertion that
raises past the restore, or `pytest -x` leaves the operator's clone pointed at a
temp directory with no error anywhere. So `scripts/run-tests.sh` scrubs the
retargeting variables AND installs `scripts/testlib/norepo.py`'s `git` shim
first on PATH, at the single point all 24 targets are invoked -- including
HOOK_TESTS and SHELL_TESTS, which no conftest can reach.

🔴 A WORKTREE IS NOT CONTAINMENT
----------------------------------
A linked worktree shares the real clone's `--git-common-dir`: refs, remotes,
config and reflog. `test_a_linked_worktree_of_a_protected_repo_is_protected`
pins that the guard resolves to the COMMON dir, because "develop this in a
worktree" is the intuitive containment answer and it is wrong -- a push from a
worktree reaches the real remote.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib import norepo  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402

RUN_TESTS = REPO_ROOT / "scripts" / "run-tests.sh"
SHIP = REPO_ROOT / "scripts" / "ship.sh"
DRIFT = REPO_ROOT / "scripts" / "drift-check.sh"
ASI_COMMIT = REPO_ROOT / "scripts" / "analyze-service-index" / "commit.sh"
DRIFT_TESTS = REPO_ROOT / "scripts" / "tests" / "test_drift_check.py"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _git(*args, cwd=None, env=None):
    return subprocess.run(["git", *args], cwd=cwd, env=env,
                          capture_output=True, text=True)


def _mkrepo(path: Path, *, bare=False, initial="main") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = ["git", "init", "-q", "-b", initial, str(path)]
    if bare:
        argv.insert(2, "--bare")
    subprocess.run(argv, check=True, capture_output=True)
    if not bare:
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(path), "config", k, v], check=True,
                           capture_output=True)
        (path / "f").write_text("x\n")
        subprocess.run(["git", "-C", str(path), "add", "f"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"],
                       check=True, capture_output=True)
    return path


@pytest.fixture
def guarded(tmp_path):
    """A victim repo with a NETWORK origin, an unprotected fixture repo, and a
    PATH with the shim in front — i.e. the runner's own arrangement, in miniature.

    🔴 The shim it installs nests INSIDE the one `run-tests.sh` already put on
    PATH. That is deliberate and harmless: the outer shim resolves this
    fixture's repos to a common dir it does not protect and forwards them, so
    what these tests observe is THIS guard's verdict. It also means a mutation
    that escaped the inner shim would still be caught by the outer one, which is
    why every assertion below checks the VICTIM'S STATE, not only the exit code.
    """
    victim = _mkrepo(tmp_path / "victim")
    subprocess.run(["git", "-C", str(victim), "remote", "add", "origin",
                    "git@github.com:innovation-upstream/devrc.git"],
                   check=True, capture_output=True)
    fixture = _mkrepo(tmp_path / "fixture")
    # 🔴 The fixture's tree must DIFFER from a victim's, or `GIT_WORK_TREE`
    # checkouts write byte-identical content and the vector looks harmless.
    # MEASURED: with both repos holding the same `f`, the GIT_WORK_TREE arm moved
    # no axis and the battery correctly called it vacuous.
    (fixture / "fixture-only.py").write_text("x = 1  # seed\n")
    subprocess.run(["git", "-C", str(fixture), "add", "fixture-only.py"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(fixture), "commit", "-qm", "fixture only"],
                   check=True, capture_output=True)
    bare = _mkrepo(tmp_path / "fixture-origin.git", bare=True)
    subprocess.run(["git", "-C", str(fixture), "remote", "add", "origin", str(bare)],
                   check=True, capture_output=True)

    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = t\n\temail = t@t\n")

    guard_dir = tmp_path / "guard"
    log = norepo.install(guard_dir,
                         norepo.protected_paths(victim),
                         [str(gitconfig)])
    env = dict(os.environ)
    env["PATH"] = f"{guard_dir}{os.pathsep}{env['PATH']}"
    env["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    env.pop("GIT_DIR", None)

    class G:
        pass

    g = G()
    g.victim, g.fixture, g.bare = victim, fixture, bare
    g.env, g.log, g.dir, g.gitconfig = env, Path(log), guard_dir, gitconfig
    g.tmp = tmp_path
    return g


def _victim_state(victim: Path) -> dict:
    def cfg(k):
        return _git("-C", str(victim), "config", "--get", k).stdout.strip()

    return {
        "core.bare": cfg("core.bare"),
        "core.hooksPath": cfg("core.hooksPath"),
        "origin": cfg("remote.origin.url"),
        "branches": sorted(
            _git("-C", str(victim), "for-each-ref", "--format=%(refname)",
                 "refs/heads").stdout.split()),
        "HEAD": _git("-C", str(victim), "symbolic-ref", "-q", "HEAD").stdout.strip(),
        "commits": len(
            _git("-C", str(victim), "log", "--oneline").stdout.splitlines()),
    }


# --------------------------------------------------------------------------- #
# 1. THE NEGATIVE CONTROL — every one of these must be REFUSED
# --------------------------------------------------------------------------- #
def _negatives(g):
    v, f, t = str(g.victim), str(g.fixture), g.tmp
    return {
        "repoint origin": ["git", "-C", v, "remote", "set-url", "origin",
                           str(t / "does-not-exist.git")],
        "set core.bare": ["git", "-C", v, "config", "core.bare", "true"],
        "re-init as bare": ["git", "-C", v, "init", "--bare"],
        "rename main": ["git", "-C", v, "branch", "-m", "main", "trunk"],
        "repoint HEAD": ["git", "-C", v, "symbolic-ref", "HEAD", "refs/heads/trunk"],
        "commit a fixture commit": ["git", "-C", v, "commit", "--allow-empty",
                                    "-m", "seed"],
        "push to the real remote": ["git", "-C", v, "push", "origin",
                                    "HEAD:refs/heads/main"],
        "set core.hooksPath": ["git", "-C", v, "config", "core.hooksPath", "/tmp/x"],
        "rewrite the global config": ["git", "config", "--global", "user.email",
                                      "evil@example.com"],
        "push a fixture to a network URL": ["git", "-C", f, "push",
                                            "https://example.com/x.git", "HEAD:main"],
    }


#: 🔴 One entry per SHAPE of the measured damage, named so a red run says which
#: half of the incident came back. The list is pinned against `_negatives`'s own
#: keys below, so a shape cannot be dropped from the battery unnoticed.
INCIDENT_SHAPES = (
    "repoint origin", "set core.bare", "re-init as bare", "rename main",
    "repoint HEAD", "commit a fixture commit", "push to the real remote",
    "set core.hooksPath", "rewrite the global config",
    "push a fixture to a network URL",
)


def test_the_incident_shape_ledger_matches_the_battery(guarded):
    assert sorted(INCIDENT_SHAPES) == sorted(_negatives(guarded)), (
        "INCIDENT_SHAPES and the negative-control battery disagree; a shape "
        "listed in one and not the other is either never run or never named.")


@pytest.mark.parametrize("name", INCIDENT_SHAPES)
def test_the_guard_refuses_every_shape_of_the_incident(guarded, name):
    """🔴 THE NEGATIVE CONTROL, one case per shape of the measured damage.

    The exit status is checked AND the refusal must name THIS guard — an
    unrelated failure (a missing binary, a bad path) also produces a non-zero
    exit, and would let a guard that had stopped working read as one that works.
    """
    before = _victim_state(guarded.victim)
    argv = _negatives(guarded)[name]
    p = subprocess.run(argv, env=guarded.env, capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert p.returncode == norepo.REFUSED_EXIT, (
        f"{name}: expected the guard's own exit {norepo.REFUSED_EXIT}, "
        f"got {p.returncode}\n{out}")
    assert "GUARD 9" in out and "norepo.py" in out, (
        f"{name}: refused, but not by THIS guard — the message must name it, or a "
        f"different failure is being read as protection:\n{out}")
    assert _victim_state(guarded.victim) == before, (
        f"{name}: the guard exited {norepo.REFUSED_EXIT} but the repository "
        f"CHANGED. A refusal that still writes is worse than no refusal.")


def test_the_guard_refuses_a_write_that_arrives_via_an_ambient_git_dir(guarded):
    """🔴 THE MEASURED MECHANISM: GIT_DIR beats `-C`.

    The argv names the FIXTURE. Only the environment names the victim. This is
    the shape no call-site audit can see, and it is why the guard resolves the
    repo the way git will rather than trusting the arguments.
    """
    before = _victim_state(guarded.victim)
    env = dict(guarded.env)
    env["GIT_DIR"] = str(guarded.victim / ".git")
    p = subprocess.run(
        ["git", "-C", str(guarded.fixture), "branch", "-m", "main", "trunk"],
        env=env, capture_output=True, text=True)
    assert p.returncode == norepo.REFUSED_EXIT, p.stdout + p.stderr
    assert "GUARD 9" in p.stdout + p.stderr
    assert _victim_state(guarded.victim) == before


# 🔴 THE ARMED-ENVIRONMENT BATTERY, AND IT PROVES ITS OWN DANGER
#
# Asserting "the victim is unchanged" under the guard is worth nothing unless
# the SAME command WITHOUT the guard demonstrably changes something. MEASURED:
# of ten vectors originally written here, FOUR moved no asserted axis at all —
# `git checkout -f HEAD` against a victim already at HEAD writes nothing
# observable whether it was refused or not, and the assertion set passed
# identically either way. That is an armed control that can only agree with
# itself, the same shape as a mutation that dies for the wrong reason.
#
# So every vector runs BOTH arms inside one test:
#   unguarded -> at least one asserted axis MUST move, or the test fails saying
#                this vector was never dangerous
#   guarded   -> refused by THIS guard, every axis unchanged
#
# `GIT_ALTERNATE_OBJECT_DIRECTORIES` is deliberately ABSENT: measured, it is a
# read-only ADDITIONAL object store and moves nothing. It is still scrubbed by
# the runner; `norepo.REFUSED_ENV` vs `RETARGETING_ENV` records that difference
# instead of implying it was tested here.
ARMED_VECTOR_IDS = (
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT",
)

#: 🔴 THE AXIS EACH VECTOR MUST MOVE — not merely "some axis".
#: MEASURED: with only "something moved", a mutant that changed the
#: GIT_CONFIG_SYSTEM vector's command to `--global` SURVIVED. Both arms still
#: passed: the global file moved, and the guard still refused. The test proved
#: a real thing about the WRONG variable, which is how a vector silently stops
#: covering what its name says.
ARMED_EXPECTED_AXIS = {
    "GIT_DIR": "branches",
    "GIT_COMMON_DIR": "HEAD",
    "GIT_WORK_TREE": "worktree",
    "GIT_INDEX_FILE": "index",
    "GIT_OBJECT_DIRECTORY": "objects",
    "GIT_CONFIG_GLOBAL": "global-config",
    "GIT_CONFIG_SYSTEM": "system-config",
    "GIT_CONFIG_COUNT": "worktree",
}


def _unguarded_env(*extra_guard_dirs) -> dict:
    """An environment with EVERY GUARD 9 shim removed from PATH.

    🔴 THE ISOLATION SEAM, MEASURED. An "unguarded" arm built from
    `dict(os.environ)` is only unguarded when nothing else installed a shim.
    Under `run-tests.sh` the RUNNER has already put its own guard dir first on
    PATH and exported it as `DEVRC_TEST_GITGUARD_DIR`, so five arms that had
    passed standalone were REFUSED by the outer guard the moment they ran in
    the real gate — the live-axis assertion then failed with "this vector is
    harmless", which is the opposite of what had happened.

    So the runner's dir is stripped by NAME from the env handle, not guessed.
    """
    env = dict(os.environ)
    drop = {str(d) for d in extra_guard_dirs if d}
    runner_dir = env.get(norepo.GUARD_DIR_ENV)
    if runner_dir:
        drop.add(runner_dir)
    env["PATH"] = os.pathsep.join(
        p for p in env["PATH"].split(os.pathsep) if p not in drop)
    # Every layer the runner adds, not just the shim: the env scrub, the
    # transport lever and the config redirect are all part of "guarded".
    for v in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
              "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
              "GIT_CONFIG_COUNT", "GIT_ALLOW_PROTOCOL",
              "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"):
        env.pop(v, None)
    return env


def _discriminating_canary(env, target, label):
    """A MUTATING write to a PROTECTED repo: refused when the guard is live.

    🔴 This is the leg whose SUCCESS is evidence of ABSENCE. Its predecessor
    committed inside an UNPROTECTED fixture -- an op the guard permits by
    design -- and MEASURED as passing both with the guard loaded and without,
    so it proved permission rather than absence.
    """
    key = f"devrc.guard9.canary{abs(hash(label)) % 100000}"
    r = subprocess.run(["git", "-C", str(target), "config", "--local", key, "1"],
                       env=env, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(target), "config", "--local", "--unset", key],
                   env=env, capture_output=True)
    return r


def _assert_truly_unguarded(env, tmp_path, label, protected_hint=None):
    """🔴 PROVE the arm is unguarded; never assume it.

    A sibling layer reported its own L2 silently re-guarding 13 "unguarded"
    controls, whose harness then dutifully reported them VACUOUS. "The victim
    did not move" would then mean STILL GUARDED, not harmless.

    🔴 AND THE POSITIVE LEG MUST DISCRIMINATE. Its first revision committed
    inside an UNPROTECTED fixture repo -- an operation the guard PERMITS BY
    DESIGN. MEASURED: it passed with the guard deliberately loaded AND with it
    absent, so its success was evidence of permission, not of absence, and it
    contributed nothing to catching a guarded arm. That is the
    instrument-answers-before-the-code-is-consulted class, one level inside the
    check built to prevent it. The legs now are:

      1. STRUCTURAL   -- no directory on PATH carries a guard ledger.
      2. NEGATIVE     -- a network call the guard WOULD refuse is not refused.
      3. DISCRIMINATING POSITIVE -- a MUTATING write to a PROTECTED repository
         succeeds. MEASURED: refused (exit 99) with the guard live, rc 0
         without it. Its success is evidence of ABSENCE.
      4. LIVENESS     -- `git --version` runs at all, which is what leg 3 was
         really testing before, now labelled honestly.

    Leg 3 needs a path the guard would protect. It is DERIVED -- from the
    caller's hint, else from the runner's own ledger -- never guessed; when
    neither exists there is no guard to detect and the leg is skipped with a
    reason rather than replaced by a permitted op.
    """
    # --- leg 1: structural ---------------------------------------------------
    for d in env.get("PATH", "").split(os.pathsep):
        if d and (Path(d) / norepo.PROTECTED_LIST_NAME).exists():
            raise AssertionError(
                f"{label}: a GUARD 9 dir is STILL on PATH ({d}); this arm is "
                "not unguarded and any 'nothing moved' from it is meaningless")

    # --- leg 2: negative canary ---------------------------------------------
    probe = subprocess.run(
        ["git", "ls-remote", "https://guard9-probe.invalid/probe.git"],
        env=env, capture_output=True, text=True, timeout=60)
    out = probe.stdout + probe.stderr
    assert probe.returncode != norepo.REFUSED_EXIT and "GUARD 9" not in out, (
        f"{label}: the negative canary was REFUSED — this arm is still guarded:\n{out}")

    # --- leg 4: liveness (cheap, and it catches a mangled PATH) --------------
    v = subprocess.run(["git", "--version"], env=env, capture_output=True, text=True)
    assert v.returncode == 0, (
        f"{label}: git does not run at all in this arm, so 'nothing moved' "
        f"proves nothing:\n{v.stdout}{v.stderr}")

    # --- leg 3: DISCRIMINATING positive canary ------------------------------
    target = protected_hint
    if target is None:
        gdir = os.environ.get(norepo.GUARD_DIR_ENV)
        if gdir:
            ledger = Path(gdir) / norepo.PROTECTED_LIST_NAME
            try:
                for line in ledger.read_text().splitlines():
                    p = Path(line.strip())
                    if p.is_dir() and (p / ".git").exists():
                        target = p
                        break
            except OSError:
                target = None
    if target is None:
        return env      # nothing protected anywhere: no guard to detect
    r = _discriminating_canary(env, target, label)
    assert r.returncode == 0 and "GUARD 9" not in (r.stdout + r.stderr), (
        f"{label}: the DISCRIMINATING canary — a write to the protected repo "
        f"{target} — was refused (rc={r.returncode}). This arm is STILL "
        f"GUARDED, so any 'nothing moved' from it is meaningless:\n"
        f"{r.stdout}{r.stderr}")
    return env


def _digest(f: Path) -> str:
    try:
        return hashlib.sha256(f.read_bytes()).hexdigest()[:12]
    except OSError:
        return "<absent>"


def _wt_digest(root: Path) -> str:
    return "|".join(
        f"{f.relative_to(root)}={_digest(f)}"
        for f in sorted(root.rglob("*"))
        if f.is_file() and ".git" not in f.parts)


def _system_cfg(gitconfig: Path) -> Path:
    """A file distinct from the global one, so a `--global` write cannot be
    mistaken for a `--system` write."""
    return gitconfig.with_name(gitconfig.name + ".system")


def _full_state(victim: Path, gitconfig: Path) -> dict:
    """Every axis the battery asserts on — wide enough that a live vector shows."""
    def q(*a):
        return _git("-C", str(victim), *a).stdout.strip()

    objs = victim / ".git" / "objects"
    return {
        "config": q("config", "--local", "--list"),
        "branches": q("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"),
        "HEAD": q("symbolic-ref", "-q", "HEAD"),
        "commits": q("rev-list", "--count", "--all"),
        "index": _digest(victim / ".git" / "index"),
        "objects": str(sum(1 for f in objs.rglob("*") if f.is_file())),
        "worktree": _wt_digest(victim),
        "global-config": _digest(gitconfig),
        "system-config": _digest(_system_cfg(gitconfig)),
    }


def _plant_hook(victim: Path) -> None:
    """A post-commit hook INSIDE the victim that writes into its working tree.

    This is what makes the GIT_CONFIG_COUNT vector observable: the injected
    `core.hooksPath` causes git to execute code out of the protected repo, and
    the marker it drops moves the `worktree` axis. Without it the vector writes
    no file anywhere and the battery cannot tell a refusal from a no-op.
    """
    hooks = victim / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    # `write_exec`, never a hand-written shebang: `scripts/tests` has a gate
    # forbidding those at runtime, because `/usr/bin/env` does not exist in the
    # nix build sandbox and the stub would fail to exec there only.
    write_exec(hooks / "post-commit", f'touch "{victim}/HOOK-EXECUTED"\n')


def _vector(vec, victim: Path, gitconfig: Path):
    """(env-to-arm, argv, stdin) for `vec`, aimed at `victim`."""
    if vec == "GIT_CONFIG_COUNT":
        _plant_hook(victim)
    return {
        "GIT_DIR": ({"GIT_DIR": str(victim / ".git")},
                    ["branch", "-m", "main", "trunk"], None),
        "GIT_COMMON_DIR": ({"GIT_COMMON_DIR": str(victim / ".git")},
                           ["branch", "-m", "main", "trunk"], None),
        "GIT_WORK_TREE": ({"GIT_WORK_TREE": str(victim)},
                          ["checkout", "-f", "HEAD"], None),
        "GIT_INDEX_FILE": ({"GIT_INDEX_FILE": str(victim / ".git" / "index")},
                           ["add", "-A", "."], None),
        # 🔴 `hash-object -w`, NOT `commit`. With GIT_OBJECT_DIRECTORY pointed at
        # the victim, a commit cannot find the FIXTURE's own objects and dies
        # rc 128 having written nothing — the vector LOOKED refused when it had
        # merely failed. `hash-object` needs no pre-existing objects, so the
        # victim's object count actually moves.
        "GIT_OBJECT_DIRECTORY": ({"GIT_OBJECT_DIRECTORY": str(victim / ".git" / "objects")},
                                 ["hash-object", "-w", "--stdin"], "payload\n"),
        "GIT_CONFIG_GLOBAL": ({"GIT_CONFIG_GLOBAL": str(gitconfig)},
                              ["config", "--global", "user.email", "evil@example.com"], None),
        # 🔴 `--system`, not `--global`. With `--global` this vector moved the
        # GLOBAL file and proved nothing about GIT_CONFIG_SYSTEM — a mislabel
        # the probe-integrity sweep caught. MEASURED: `config --system` with
        # GIT_CONFIG_SYSTEM set does write that file (rc 0, contents changed).
        "GIT_CONFIG_SYSTEM": ({"GIT_CONFIG_SYSTEM": str(_system_cfg(gitconfig))},
                              ["config", "--system", "user.email", "evil@example.com"], None),
        # 🔴 The channel a file-watching guard cannot see: this writes NO
        # config file anywhere. MEASURED with `core.worktree` first, which moved
        # NOTHING (git ignores it for a `-C`-discovered repo) — a vector that
        # would have been "refused" while being harmless. `core.hooksPath` at
        # the victim's OWN hooks dir is the live form: the planted hook RUNS and
        # writes into the victim's working tree, which `_full_state` observes.
        "GIT_CONFIG_COUNT": ({"GIT_CONFIG_COUNT": "1",
                              "GIT_CONFIG_KEY_0": "core.hooksPath",
                              "GIT_CONFIG_VALUE_0": str(victim / ".git" / "hooks")},
                             ["commit", "--allow-empty", "-m", "x"], None),
    }[vec]


@pytest.mark.parametrize("vec", ARMED_VECTOR_IDS)
def test_the_armed_vector_is_DANGEROUS_and_the_guard_HOLDS(guarded, tmp_path, vec):
    """🔴 BOTH ARMS IN ONE TEST, so they cannot drift apart.

    Each arm gets its OWN victim, because arm 1 is expected to damage one.
    """
    # --- arm 1: UNGUARDED. Prove the vector can actually do damage. -----------
    v1 = _mkrepo(tmp_path / "victim-unguarded")
    cfg1 = tmp_path / "gitconfig-unguarded"
    cfg1.write_text("[user]\n\tname = t\n\temail = t@t\n")
    _system_cfg(cfg1).write_text("")
    env_add, argv, stdin = _vector(vec, v1, cfg1)
    unguarded = _unguarded_env(guarded.dir)
    _assert_truly_unguarded(unguarded, tmp_path, f"armed-{vec}")
    unguarded["GIT_CONFIG_GLOBAL"] = str(cfg1)
    unguarded.update(env_add)
    before = _full_state(v1, cfg1)
    u = subprocess.run(["git", "-C", str(guarded.fixture), *argv], env=unguarded,
                       input=stdin, capture_output=True, text=True)
    after = _full_state(v1, cfg1)
    moved = [k for k in before if before[k] != after[k]]
    assert moved, (
        f"{vec}: UNGUARDED this command moved NO asserted axis (rc={u.returncode}). "
        "The vector is harmless, so refusing it proves nothing — replace it with "
        f"a command that actually writes.\n{u.stdout}{u.stderr}")
    want = ARMED_EXPECTED_AXIS[vec]
    assert want in moved, (
        f"{vec}: moved {moved}, but not its OWN axis `{want}`. The vector is "
        f"exercising something real about a DIFFERENT mechanism — which is how "
        f"a named vector silently stops covering what it claims.\n"
        f"{u.stdout}{u.stderr}")

    # --- arm 2: GUARDED. Same command, fresh victim, guard installed. --------
    v2 = _mkrepo(tmp_path / "victim-guarded")
    cfg2 = tmp_path / "gitconfig-guarded"
    cfg2.write_text("[user]\n\tname = t\n\temail = t@t\n")
    _system_cfg(cfg2).write_text("")
    env_add2, argv2, stdin2 = _vector(vec, v2, cfg2)
    gd = tmp_path / "guard2"
    norepo.install(gd, norepo.protected_paths(v2),
                   [str(cfg2), str(_system_cfg(cfg2))])
    genv = dict(os.environ)
    genv.pop("GIT_DIR", None)
    genv["PATH"] = f"{gd}{os.pathsep}" + os.environ["PATH"]
    genv["GIT_CONFIG_GLOBAL"] = str(cfg2)
    genv.update(env_add2)
    b2 = _full_state(v2, cfg2)
    p = subprocess.run(["git", "-C", str(guarded.fixture), *argv2], env=genv,
                       input=stdin2, capture_output=True, text=True)
    a2 = _full_state(v2, cfg2)
    out = p.stdout + p.stderr
    assert p.returncode == norepo.REFUSED_EXIT, (
        f"{vec}: GUARDED, expected the guard's exit {norepo.REFUSED_EXIT}, got "
        f"{p.returncode}. The unguarded arm moved {moved}, so this is a live "
        f"bypass.\n{out}")
    assert "GUARD 9" in out, f"{vec}: refused, but not by this guard:\n{out}"
    assert a2 == b2, (
        f"{vec}: the guard exited {norepo.REFUSED_EXIT} but the victim CHANGED: "
        f"{[k for k in b2 if b2[k] != a2[k]]}")


def test_the_armed_ledger_distinguishes_REFUSED_from_merely_SCRUBBED():
    """🔴 BOTH WAYS, AGAINST THE SHIM ITSELF — not against a second list.

    `RETARGETING_ENV` was enumerated from `man git` and is INCOMPLETE BY
    CONSTRUCTION: `GIT_CONFIG_COUNT` is undocumented, honoured, and was missing
    from it until a peer session reproduced the bypass. It is a BACKSTOP.
    `REFUSED_ENV` is the subset the shim acts on.

    🔴 MEASURED: the first version of this test only computed
    `REFUSED_ENV - armed`, so DELETING an entry made the set smaller and the
    test PASSED. A mutant that dropped `GIT_CONFIG_COUNT` survived. A ledger
    that only catches additions is half a pin, so the truth is now read out of
    the SHIM TEXT and compared in both directions.
    """
    shim = norepo.shim_body("/usr/bin/git", Path("/tmp/l"), Path("/tmp/p"),
                            Path("/tmp/c"))

    # The variables the shim's environment loop actually branches on.
    m = re.search(r"for _v in ((?:[A-Z_ ]|\\\n\s*)+); do", shim)
    assert m, "the shim's armed-environment loop is gone entirely"
    in_shim = set(m.group(1).replace("\\", " ").split())

    # ...plus the one that is checked by its own block rather than the loop,
    # because it is indexed (KEY_<n>/VALUE_<n>) rather than a single value.
    assert 'case "${GIT_CONFIG_COUNT:-}" in' in shim, (
        "the shim no longer inspects GIT_CONFIG_COUNT. That channel writes NO "
        "file, so neither the config redirect nor path protection can see it.")
    in_shim.add("GIT_CONFIG_COUNT")

    assert set(norepo.REFUSED_ENV) == in_shim, (
        "REFUSED_ENV and the shim disagree about what is refused.\n"
        f"  ledger: {sorted(norepo.REFUSED_ENV)}\n"
        f"  shim  : {sorted(in_shim)}\n"
        "Do NOT delete a ledger entry to make this pass.")

    armed = set(ARMED_VECTOR_IDS)
    not_a_write_vector = {"GIT_ALTERNATE_OBJECT_DIRECTORIES"}
    missing = set(norepo.REFUSED_ENV) - armed - not_a_write_vector
    assert not missing, (
        f"{sorted(missing)} are REFUSED by the shim but never armed against a "
        "victim, so their protection is a claim about setup only.")
    assert set(norepo.REFUSED_ENV) <= set(norepo.RETARGETING_ENV), (
        "the shim refuses a variable the runner does not scrub")
    harmless = {"GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES", "GIT_PREFIX"}
    assert not (harmless & set(norepo.REFUSED_ENV)), (
        f"{sorted(harmless & set(norepo.REFUSED_ENV))} are NOT write targets; "
        "refusing them would be a pure false positive and a permanently-red gate.")


def test_a_linked_worktree_of_a_protected_repo_is_protected(guarded):
    """🔴 A WORKTREE IS NOT CONTAINMENT — the common dir is the unit.

    Resolving `--git-dir` instead of `--git-common-dir` would report this
    worktree as a different, unprotected repository, while a write in it lands
    on the real clone's refs and a push from it reaches the real remote.
    """
    wt = guarded.tmp / "victim-wt"
    subprocess.run(["git", "-C", str(guarded.victim), "worktree", "add", "-q",
                    "-b", "wt", str(wt)], check=True, capture_output=True)
    before = _victim_state(guarded.victim)
    p = subprocess.run(["git", "-C", str(wt), "branch", "-m", "wt", "wt2"],
                       env=guarded.env, capture_output=True, text=True)
    assert p.returncode == norepo.REFUSED_EXIT, p.stdout + p.stderr
    assert _victim_state(guarded.victim) == before


def test_a_write_with_no_dash_C_from_inside_the_protected_repo_is_refused(guarded):
    """The cwd route: `run-tests.sh` runs every pytest target with cwd = the repo
    root, so a git call that binds NEITHER `-C` nor `cwd=` lands in the real
    clone. Nothing in this tree does that today; the guard does not depend on
    that staying true."""
    before = _victim_state(guarded.victim)
    p = subprocess.run(["git", "config", "core.bare", "true"],
                       cwd=str(guarded.victim), env=guarded.env,
                       capture_output=True, text=True)
    assert p.returncode == norepo.REFUSED_EXIT, p.stdout + p.stderr
    assert _victim_state(guarded.victim) == before


# --------------------------------------------------------------------------- #
# 2. THE POSITIVE CONTROL — the guard must DISCRIMINATE, not just refuse
# --------------------------------------------------------------------------- #
POSITIVE_MUTATIONS = [
    ("commit in a fixture repo", ["commit", "--allow-empty", "-m", "ok"]),
    ("rename a fixture branch", ["branch", "-m", "main", "renamed"]),
    ("repoint a fixture remote", ["remote", "set-url", "origin", "REPLACED"]),
    ("push a fixture to its LOCAL bare origin",
     ["push", "-q", "origin", "HEAD:refs/heads/main"]),
]


@pytest.mark.parametrize("name,args", POSITIVE_MUTATIONS, ids=[n for n, _ in POSITIVE_MUTATIONS])
def test_a_fixture_repo_may_still_be_mutated_freely(guarded, name, args):
    """🔴 THE POSITIVE CONTROL. A guard that refuses everything is
    indistinguishable from a guard that works, and it would be a permanently-red
    gate — the failure mode `claude/RULES.md` says trains everyone to click
    through. Every one of these is a MUTATION, on a repo the guard does not
    protect, and it must go through untouched."""
    args = [str(guarded.bare) if a == "REPLACED" else a for a in args]
    p = subprocess.run(["git", "-C", str(guarded.fixture), *args],
                       env=guarded.env, capture_output=True, text=True)
    assert p.returncode == 0, f"{name} was broken by the guard:\n{p.stdout}{p.stderr}"


POSITIVE_READS = [
    ("ls-files", ["ls-files"]),
    ("log", ["log", "--oneline", "-1"]),
    ("status", ["status", "--porcelain"]),
    ("rev-parse", ["rev-parse", "HEAD"]),
    ("branch --show-current", ["branch", "--show-current"]),
    ("remote -v", ["remote", "-v"]),
    ("config --get", ["config", "--get", "core.bare"]),
    ("symbolic-ref -q HEAD", ["symbolic-ref", "-q", "HEAD"]),
    ("worktree list", ["worktree", "list"]),
]


@pytest.mark.parametrize("name,args", POSITIVE_READS, ids=[n for n, _ in POSITIVE_READS])
def test_a_protected_repo_may_still_be_READ(guarded, name, args):
    """Several gates in this repo read the tree under test. Refusing those would
    make the guard unshippable, so the read forms are enumerated and pinned."""
    p = subprocess.run(["git", "-C", str(guarded.victim), *args],
                       env=guarded.env, capture_output=True, text=True)
    assert p.returncode == 0, f"read `{name}` was refused:\n{p.stdout}{p.stderr}"


def test_a_fixture_repo_may_be_mutated_with_the_environment_vectors_ARMED_AT_ITSELF(
        guarded):
    """🔴 THE POSITIVE CONTROL FOR THE ARMED BATTERY, and the one that proves the
    environment check DISCRIMINATES rather than banning the variables outright.

    The same variables, set to the FIXTURE's own paths, must still work: this is
    how `git` is legitimately driven by tooling, and a guard that refused
    `GIT_DIR=<my own fixture>` would be a permanently-red gate. Reported beside
    the armed battery as the pair — refused when aimed at the victim, allowed
    when aimed at itself.
    """
    env = dict(guarded.env)
    env["GIT_DIR"] = str(guarded.fixture / ".git")
    env["GIT_WORK_TREE"] = str(guarded.fixture)
    p = subprocess.run(["git", "commit", "--allow-empty", "-m", "own-fixture"],
                       cwd=str(guarded.fixture), env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, (
        "the guard refused a fixture driving git at its OWN repo through the "
        f"environment; that is a permanently-red gate:\n{p.stdout}{p.stderr}")


def test_the_positive_control_examines_a_reported_number_of_cases():
    """🔴 Report the COUNT, never a bare "the positive control passed". A
    positive control that shrank to zero cases still reports success."""
    assert len(POSITIVE_MUTATIONS) >= 4, POSITIVE_MUTATIONS
    assert len(POSITIVE_READS) >= 9, POSITIVE_READS
    assert len(ARMED_VECTOR_IDS) == 8, ARMED_VECTOR_IDS
    assert len(INCIDENT_SHAPES) == 10, INCIDENT_SHAPES


def test_git_init_with_a_RELATIVE_dest_resolves_against_dash_C_not_the_cwd(guarded):
    """🔴 A MEASURED FALSE POSITIVE, pinned so it cannot come back.

    `git -C X init .` means "init X": git chdirs to X BEFORE reading the
    positional. Resolving that `.` against the SHIM's cwd — which under
    `run-tests.sh` is the repo root — made the guard refuse
    `git -C <tmp_path>/plain-repo init -q -b trunk .`, a completely legitimate
    fixture. It was caught by GUARD 9's own per-target accounting on a full
    run, not by any test, which is exactly what that accounting is for.

    A guard that refuses legitimate work is a permanently-red gate, and
    `claude/RULES.md` is explicit that those train everyone to click through.
    """
    dest = guarded.tmp / "plain-repo"
    dest.mkdir()
    p = subprocess.run(["git", "-C", str(dest), "init", "-q", "-b", "trunk", "."],
                       cwd=str(guarded.victim), env=guarded.env,
                       capture_output=True, text=True)
    assert p.returncode == 0, (
        "a fixture's own `git -C <tmp> init .` was refused; the relative "
        f"destination is being resolved against the wrong directory:\n{p.stdout}{p.stderr}")
    assert (dest / ".git").exists(), "the repo was not actually created"
    # ...and the protected repo is untouched by it.
    assert _victim_state(guarded.victim)["branches"] == ["refs/heads/main"]


def test_git_init_and_clone_of_new_fixture_paths_still_work(guarded):
    """`init`/`clone` name a destination that is not a repo yet, so the guard
    resolves them from that destination. Getting that wrong in the safe
    direction breaks every fixture in the suite."""
    for argv in (["git", "init", "-q", "-b", "main", str(guarded.tmp / "newfix")],
                 ["git", "clone", "-q", str(guarded.bare), str(guarded.tmp / "cloned")]):
        p = subprocess.run(argv, env=guarded.env, capture_output=True, text=True)
        assert p.returncode == 0, f"{argv}:\n{p.stdout}{p.stderr}"


def test_the_ledger_records_the_refusal_and_says_what_it_was(guarded):
    """A refusal that is not RECORDED cannot be attributed to a target, and
    `run-tests.sh` would print a clean zero for the target that caused it."""
    subprocess.run(["git", "-C", str(guarded.victim), "config", "core.bare", "true"],
                   env=guarded.env, capture_output=True, text=True)
    lines = guarded.log.read_text().splitlines()
    assert len(lines) == 1, f"expected exactly one ledger line, got: {lines}"
    assert lines[0].startswith(norepo.REFUSED_PREFIX)
    assert "PROTECTED" in lines[0] and "core.bare" in lines[0]


def test_the_probe_env_records_a_CONTROL_and_still_refuses(guarded):
    """🔴 The per-target positive control the runner counts. It must not weaken
    the refusal — a control that let the write through would be the guard
    proving it works by not working."""
    before = _victim_state(guarded.victim)
    env = dict(guarded.env)
    env[norepo.PROBE_ENV] = "1"
    p = subprocess.run(["git", "-C", str(guarded.victim), "config", "core.bare", "true"],
                       env=env, capture_output=True, text=True)
    assert p.returncode == norepo.REFUSED_EXIT
    assert _victim_state(guarded.victim) == before
    lines = guarded.log.read_text().splitlines()
    assert lines and lines[0].startswith(norepo.CONTROL_PREFIX), lines


def test_install_refuses_to_arm_against_nothing_without_an_explicit_flag(tmp_path):
    """🔴 An empty protected list is a guard wired to nothing, and its zero
    refusals read exactly like a clean run. Saying so must be deliberate."""
    with pytest.raises(RuntimeError, match="protects"):
        norepo.install(tmp_path / "g1", [], [])
    norepo.install(tmp_path / "g2", [], [], allow_no_repos=True)  # the sandbox door


# --------------------------------------------------------------------------- #
# 3. THE `${VAR:-default}` SITES — pinned BOTH ways
# --------------------------------------------------------------------------- #
#: "<file>|<the assignment line, verbatim>|<the variable the guard must test>".
#: A ledger, not a convenience list: the test below fails when a site GROWS
#: (a new HOME-defaulting repo path with no set-but-empty guard) *or* SHRINKS
#: (an entry naming a line that no longer exists — deleted, renamed, or a typo).
EMPTY_DEFAULT_SITES = (
    ("scripts/ship.sh", 'SHIP_REPO="${SHIP_REPO:-$HOME/workspace/devrc}"', "SHIP_REPO"),
    ("scripts/ship.sh", 'repo="${SHIP_REPO:-$HOME/workspace/devrc}"', "SHIP_REPO"),
    ("scripts/drift-check.sh", 'DRIFT_REPO="${DRIFT_REPO:-$HOME/workspace/devrc}"', "DRIFT_REPO"),
    # TWICE, deliberately: the CHECK payload and the SRCREPO payload each carry
    # their own copy, because each is piped to a `bash -s` on ANOTHER host and
    # cannot source anything. Both need their own guard.
    ("scripts/drift-check.sh", 'repo="${DRIFT_REPO:-$HOME/workspace/devrc}"', "DRIFT_REPO"),
    ("scripts/drift-check.sh", 'repo="${DRIFT_REPO:-$HOME/workspace/devrc}"', "DRIFT_REPO"),
    ("scripts/analyze-service-index/commit.sh",
     'STORE="${POSITIONAL[0]:-${HOME}/.claude/analyze-service-index}"', "POSITIONAL"),
)

#: Any `${SOMETHING:-…$HOME…}` / `${…:-…~/…}` that names a path this repo's
#: scripts then run git against. The scan below finds them mechanically so a
#: SIXTH site cannot be added without either a guard or a ledger entry.
_HOME_DEFAULT_RE = re.compile(
    r'^\s*\w+="\$\{[A-Za-z_][A-Za-z0-9_]*(?:\[0\])?:-\$?\{?HOME\}?/[^"]*'
    r'(?:workspace/devrc|\.claude/analyze-service-index)[^"]*"\s*$')


def _sites_in(path: Path):
    return [(n, ln.rstrip("\n")) for n, ln in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1)
        if _HOME_DEFAULT_RE.match(ln)]


def test_every_home_defaulting_repo_path_is_pinned_in_the_ledger():
    """🔴 BOTH WAYS. A new site with no ledger entry is a new copy of the bug; a
    ledger entry naming no line is accounting that describes nothing."""
    found = []
    for rel in sorted({s[0] for s in EMPTY_DEFAULT_SITES}):
        for _, line in _sites_in(REPO_ROOT / rel):
            found.append((rel, line.strip()))
    pinned = [(f, line) for f, line, _ in EMPTY_DEFAULT_SITES]
    assert sorted(found) == sorted(pinned), (
        "the HOME-defaulting repo-path sites and EMPTY_DEFAULT_SITES disagree.\n"
        f"  on disk: {sorted(found)}\n"
        f"  pinned : {sorted(pinned)}\n"
        "Do NOT delete an entry to make this pass — every one of these silently "
        "resolves to the operator's own clone when its variable is set-but-EMPTY.")


@pytest.mark.parametrize(
    "rel,line,var", sorted(set(EMPTY_DEFAULT_SITES)),
    ids=[f"{f.split('/')[-1]}:{v}" for f, _, v in sorted(set(EMPTY_DEFAULT_SITES))])
def test_each_site_is_immediately_preceded_by_a_set_but_empty_guard(rel, line, var):
    """🔴 `${VAR:-default}` CANNOT TELL UNSET FROM EMPTY. Unset must keep
    defaulting — the remote legs deliberately do not forward these variables —
    but a set-but-EMPTY value is a caller bug that would silently target the
    operator's own clone, and it must stop the run.

    🔴 EVERY OCCURRENCE, not the first. Two of these lines are byte-identical
    (`drift-check.sh`'s CHECK and SRCREPO payloads), and checking only
    `text.index(line)` would have declared the second one guarded on the
    strength of the first — a guard's DESCRIPTION claiming coverage its body
    does not provide.
    """
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(re.escape(line), text)]
    assert starts, f"{rel}: `{line}` is not in the file at all"
    for n, idx in enumerate(starts, 1):
        # The guard must be the NEAREST thing above the assignment, not somewhere
        # else in the file: 25 lines is generous for the comment block plus the
        # test, and small enough that a distant unrelated guard cannot satisfy it.
        window = "\n".join(text[:idx].splitlines()[-25:])
        where = f"{rel}: occurrence {n}/{len(starts)} of `{line}`"
        if var == "POSITIONAL":
            assert '[ -z "${POSITIONAL[0]}" ]' in window, (
                f"{where} has no given-but-EMPTY guard above it.")
        else:
            assert f'"${{{var}+set}}" = set' in window and f'[ -z "${var}" ]' in window, (
                f"{where} has no set-but-EMPTY guard above it. Without one, "
                f"{var}='' resolves to $HOME/workspace/devrc.")


@pytest.mark.parametrize("script,var", [(SHIP, "SHIP_REPO"), (DRIFT, "DRIFT_REPO")],
                         ids=["ship.sh", "drift-check.sh"])
def test_an_empty_repo_override_stops_the_run_instead_of_targeting_the_real_clone(
        tmp_path, script, var):
    """🔴 BEHAVIOURAL, not structural. A structural check type-checks past a
    guard that is present but wrong.

    The fake HOME contains a REAL repository at `workspace/devrc` — the place an
    empty value resolves to. If the guard were absent the script would fetch and
    report on it; with the guard it must exit non-zero having said the variable
    is empty, and that repo must be untouched.
    """
    home = tmp_path / "home"
    victim = _mkrepo(home / "workspace" / "devrc")
    before = _victim_state(victim)
    env = dict(os.environ)
    env.update(HOME=str(home), SHIP_ROLE="workbench", GIT_CONFIG_GLOBAL=str(tmp_path / "gc"))
    env[var] = ""
    p = subprocess.run(["bash", str(script), "--no-remote"], env=env,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    # 🔴 THE EXACT STATUS, not merely non-zero. MEASURED: a mutant that kept the
    # message and dropped the `exit 2` SURVIVED an earlier version of this test
    # — the script carried on, defaulted to $HOME/workspace/devrc, converged the
    # victim, and failed later for an unrelated reason, so "non-zero" and
    # "EMPTY appears in the output" were both still true. A green for the wrong
    # reason is the failure this whole PR is about.
    assert p.returncode == 2, (
        f"an EMPTY {var} did not stop the run with exit 2 (got {p.returncode}). "
        f"If the guard printed but did not exit, the script continued to the "
        f"default:\n{out}")
    assert "EMPTY" in out, f"the run stopped, but not for this reason:\n{out}"
    # 🔴 AND NOTHING DOWNSTREAM RAN. Both scripts announce each host before doing
    # any work — `=== local (…) ===` and `[<host>] …`. Either line means the
    # guard did not stop the run, whatever the exit status said.
    started = [ln for ln in out.splitlines()
               if ln.startswith("===") or ln.lstrip().startswith("[")]
    assert not started, (
        f"{script.name} began operating on a host despite {var}='':\n"
        + "\n".join(started))
    assert _victim_state(victim) == before, (
        f"{script.name} touched $HOME/workspace/devrc despite {var}=''")


def test_an_unset_repo_override_still_defaults(tmp_path):
    """The other direction, and it is load-bearing: the remote legs deliberately
    do NOT forward these variables, so an UNSET value must keep resolving to
    `$HOME/workspace/devrc`. A guard that failed on unset too would break the
    remote leg of both scripts."""
    home = tmp_path / "home"
    _mkrepo(home / "workspace" / "devrc")
    env = dict(os.environ)
    env.update(HOME=str(home), SHIP_ROLE="workbench",
               GIT_CONFIG_GLOBAL=str(tmp_path / "gc"))
    env.pop("DRIFT_REPO", None)
    env["DRIFT_STATE_DIR"] = str(tmp_path / "state")
    env["DRIFT_SESSION_MANAGER"] = str(tmp_path / "no-such-session-manager")
    p = subprocess.run(["bash", str(DRIFT), "--no-remote"], env=env,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert "SET but EMPTY" not in out, f"unset was treated as empty:\n{out}"
    assert "no repo at" not in out, (
        f"the default no longer resolves to $HOME/workspace/devrc:\n{out}")


# --------------------------------------------------------------------------- #
# 3b. THE TWO LEVERS THAT DO NOT DEPEND ON THE SHIM BEING REACHED
#
# The shim intercepts by PATH, so an absolute `/usr/bin/git` or a test that
# REPLACES $PATH walks past it. These two are enforced by git itself, in-process.
# Adapted from devrc-62's `testlib/nogit_plugin` (PR #673, not merged).
# --------------------------------------------------------------------------- #
NETWORK_URLS = (
    "git@github.com:innovation-upstream/devrc.git",
    "https://github.com/innovation-upstream/devrc.git",
    "ssh://git@github.com/innovation-upstream/devrc.git",
)


@pytest.mark.parametrize("url", NETWORK_URLS)
def test_a_fixture_push_to_a_real_remote_url_is_refused_by_the_shim(guarded, url):
    """🔴 The source repo is INNOCENT and the destination is a URL, so a guard
    that reasoned only about protected PATHS would be blind to this — and this
    is the half of the incident that reached the public repo.

    MEASURED on this box with the shim OFF PATH: the scp-style push printed
    `To github.com:innovation-upstream/devrc.git`, a push PROGRESS header. It
    authenticated and CONNECTED over ssh and failed only because the update was
    rejected. One fast-forward away from landing.
    """
    p = subprocess.run(["git", "-C", str(guarded.fixture), "push", url,
                        "HEAD:refs/heads/main"],
                       env=guarded.env, capture_output=True, text=True, timeout=120)
    out = p.stdout + p.stderr
    assert p.returncode == norepo.REFUSED_EXIT, f"{url} was not refused:\n{out}"
    assert "GUARD 9" in out, f"{url} failed, but not by this guard:\n{out}"
    assert "NETWORK" in out, f"the refusal did not name the network rule:\n{out}"


@pytest.mark.parametrize("url", NETWORK_URLS)
def test_GIT_ALLOW_PROTOCOL_refuses_the_transport_ATTRIBUTABLY(guarded, url):
    """🔴 THE DISCRIMINATING CONTROL, and the reason `assert rc != 0` is useless
    here.

    WITHOUT the lever these pushes ALREADY exit non-zero — by REJECTION (rc 1,
    having connected) or a credential prompt (rc 128). So a test that only
    checked the status would pass whether or not the lever did anything, and
    would keep passing if someone deleted it.

    The refusal is attributable ONLY when git names the transport. This runs
    with the shim REMOVED from PATH, so what it measures is the lever alone.
    """
    env = dict(guarded.env)
    gd = env.get(norepo.GUARD_DIR_ENV) or str(guarded.dir)
    env["PATH"] = os.pathsep.join(
        x for x in env["PATH"].split(os.pathsep)
        if x != gd and x != str(guarded.dir))
    env["GIT_ALLOW_PROTOCOL"] = "file"
    p = subprocess.run(["git", "-C", str(guarded.fixture), "push", url,
                        "HEAD:refs/heads/main"],
                       env=env, capture_output=True, text=True, timeout=120)
    out = p.stdout + p.stderr
    assert "not allowed" in out, (
        "GIT_ALLOW_PROTOCOL=file did not refuse this transport by name. A "
        f"non-zero exit alone is NOT evidence — it happens anyway:\n{out}")
    assert p.returncode != 0


def test_GIT_ALLOW_PROTOCOL_still_permits_every_LOCAL_fixture_operation(guarded):
    """The positive control for the lever. A transport allowlist that broke
    `file://` or plain-path fixtures would be a permanently-red gate."""
    env = dict(guarded.env)
    env["GIT_ALLOW_PROTOCOL"] = "file"
    for name, argv in (
        ("push to a plain-path bare",
         ["-C", str(guarded.fixture), "push", "-q", str(guarded.bare), "HEAD:refs/heads/m1"]),
        ("push to a file:// bare",
         ["-C", str(guarded.fixture), "push", "-q", f"file://{guarded.bare}", "HEAD:refs/heads/m2"]),
        ("clone from a plain-path bare",
         ["clone", "-q", str(guarded.bare), str(guarded.tmp / "lever-clone")]),
    ):
        p = subprocess.run(["git", *argv], env=env, capture_output=True, text=True)
        assert p.returncode == 0, f"the lever broke `{name}`:\n{p.stdout}{p.stderr}"


def test_the_runner_sets_both_levers_and_redirects_the_config_files():
    """🔴 Pinned in the RUNNER, because that is the only place that covers the
    non-pytest targets. A lever set in a conftest protects one directory."""
    text = RUN_TESTS.read_text(encoding="utf-8")
    # 🔴 AN `export` STATEMENT AT LINE START, never a substring search.
    # MEASURED: the first version of this test was `token in text`, and a mutant
    # that renamed the export to `GIT_ALLOW_PROTOCOL_DISABLED=file` SURVIVED —
    # because the COMMENT block above it still spelled `GIT_ALLOW_PROTOCOL=file`
    # in prose. The test was reading the documentation and reporting on the code.
    # `^export` cannot match a `#` line, so prose can no longer satisfy it.
    for token in ("GIT_ALLOW_PROTOCOL=file", "GIT_CONFIG_SYSTEM=/dev/null",
                  "GIT_CONFIG_NOSYSTEM=1", 'GIT_CONFIG_GLOBAL="$GITCONFIG_TRAP"'):
        assert re.search(rf"^export {re.escape(token)}\s*$", text, re.M), (
            f"run-tests.sh has no `export {token}` statement (a mention in a "
            "comment does not count)")


def test_the_config_redirect_happens_AFTER_the_guard_is_installed():
    """🔴 ORDER IS THE WHOLE THING. Deriving the protected config set after the
    redirect would protect the throwaway file and leave `~/.gitconfig`
    unprotected — the guard pointed at the decoy it just created."""
    text = RUN_TESTS.read_text(encoding="utf-8")
    assert text.index("python -m testlib.norepo") < text.index('export GIT_CONFIG_GLOBAL=')


def test_the_real_global_config_is_protected_even_when_the_env_is_redirected(tmp_path,
                                                                             monkeypatch):
    """🔴 The complement of the ordering test, at the level of the code rather
    than the file. With GIT_CONFIG_GLOBAL pointed at a throwaway — exactly what
    the runner does — `~/.gitconfig` must STILL be in the protected set."""
    decoy = tmp_path / "decoy-gitconfig"
    decoy.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(decoy))
    monkeypatch.setattr(norepo.sys if hasattr(norepo, "sys") else os, "environ",
                        os.environ, raising=False)
    guard = tmp_path / "g"
    repo = _mkrepo(tmp_path / "r")
    rc = norepo.main([str(guard), str(repo)])
    assert rc == 0
    listed = (guard / norepo.PROTECTED_CONFIG_NAME).read_text().splitlines()
    real = os.path.realpath(Path.home() / ".gitconfig")
    assert real in listed, (
        f"~/.gitconfig ({real}) is NOT protected when GIT_CONFIG_GLOBAL is "
        f"redirected. Protected set was: {listed}")
    assert os.path.realpath(decoy) in listed, "the redirected file should be protected too"


# --------------------------------------------------------------------------- #
# 3c. TWO ADJACENT HAZARDS THIS GUARD NOW COVERS
#
# Neither is fixed by this PR. Both are live shapes in the tree that GUARD 9
# turns from "safe by accident" into "safe by construction", and pinning them
# here is what makes that claim checkable rather than asserted.
# --------------------------------------------------------------------------- #
ADJACENT_HAZARDS = [
    # scripts/repo-cos/prescan.py drives these against every repo in
    # scan.py's DEFAULT_REPOS, whose FIRST entry is `~/workspace/devrc`.
    # Every current test monkeypatches that list away — the hazard is one
    # deleted patch from being live.
    ("repo-cos prescan: worktree add", ["worktree", "add", "--detach", "/tmp/x"]),
    ("repo-cos prescan: worktree remove", ["worktree", "remove", "--force", "/tmp/x"]),
    ("repo-cos prescan: worktree prune", ["worktree", "prune"]),
    ("repo-cos prescan: fetch origin", ["fetch", "--quiet", "origin"]),
    # ship.sh's CONVERGE, which `test_drift_check.py` invokes with the REAL
    # $HOME and no SHIP_REPO. It is safe today only because an unrelated
    # exit-6 path fires first — safe by accident.
    ("ship.sh CONVERGE: fetch origin", ["fetch", "origin", "-q"]),
    ("ship.sh CONVERGE: merge --ff-only", ["merge", "--ff-only", "origin/main"]),
]


@pytest.mark.parametrize("name,args", ADJACENT_HAZARDS,
                         ids=[n for n, _ in ADJACENT_HAZARDS])
def test_the_adjacent_hazards_are_refused_against_a_protected_repo(guarded, name, args):
    """🔴 `git worktree list` is a READ and stays allowed; `worktree add`,
    `remove`, `prune`, `fetch` and `merge` are not, and none of them is on the
    read-form list. So a prescan or a converge pointed at the operator's clone
    is refused rather than merely unlikely."""
    before = _victim_state(guarded.victim)
    p = subprocess.run(["git", "-C", str(guarded.victim), *args],
                       env=guarded.env, capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert p.returncode == norepo.REFUSED_EXIT, f"{name} was NOT refused:\n{out}"
    assert "GUARD 9" in out
    assert _victim_state(guarded.victim) == before


def test_worktree_list_is_still_a_permitted_read(guarded):
    """The other direction: prescan's own bookkeeping read must keep working, or
    the guard is a permanently-red gate for the very tool it protects."""
    p = subprocess.run(["git", "-C", str(guarded.victim), "worktree", "list"],
                       env=guarded.env, capture_output=True, text=True)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"


# --------------------------------------------------------------------------- #
# 3d. THE ADVERSARIAL-AUDIT BYPASSES
#
# 🔴 SIX of these were MEASURED to work against an earlier revision of this
# shim, from an INNOCENT fixture repo, with the guard fully installed. They are
# pinned here as their own battery because each attacks a DIFFERENT assumption:
#   * the alias pair  -- git prepends its own libexec to PATH, so the shim is
#                        NOT inherited by git-spawned children, at any depth
#   * `config --file` -- a config file named DIRECTLY, in an innocent repo
#   * `-c remote.*.url` -- config injected on the argv, which a resolver that
#                        shells a FRESH `git config` cannot see
#   * `--separate-git-dir` / `worktree add` -- a DESTINATION inside a denied
#                        tree, from a repo that resolves as innocent
#   * `status`        -- a "read" that rewrites `.git/index`
#
# Each asserts the guard's own exit AND that the victim is byte-unchanged: an
# unrelated failure also exits non-zero, and would let a dead guard read as a
# live one.
AUDIT_BYPASSES = [
    ("alias shell-escape via -c", "refuse",
     lambda v, f: ["-C", str(f), "-c",
                   f"alias.x=!git -C {v} commit --allow-empty -m PWNED", "x"]),
    ("alias shell-escape via GIT_CONFIG_COUNT", "refuse-env",
     lambda v, f: ["-C", str(f), "y"]),
    ("config --file into the victim", "refuse",
     lambda v, f: ["-C", str(f), "config", "--file",
                   str(v / ".git" / "config"), "core.bare", "true"]),
    ("config -f into the victim", "refuse",
     lambda v, f: ["-C", str(f), "config", "-f",
                   str(v / ".git" / "config"), "user.email", "evil@example.com"]),
    ("-c remote.origin.url=<network> push", "refuse",
     lambda v, f: ["-C", str(f), "-c",
                   "remote.origin.url=git@github.com:innovation-upstream/devrc.git",
                   "push", "origin", "HEAD:refs/heads/main"]),
    ("--separate-git-dir into the victim", "refuse",
     lambda v, f: ["init", "--separate-git-dir", str(v / "planted-gitdir"),
                   str(f.parent / "sep-clone")]),
    ("worktree add into the victim", "refuse",
     lambda v, f: ["-C", str(f), "worktree", "add", "--detach",
                   str(v / "planted-worktree")]),
    ("hash-object -w into the victim", "refuse",
     lambda v, f: ["-C", str(v), "hash-object", "-w", "--stdin"]),
    ("GIT_SSH_COMMAND escape on a network push", "refuse",
     lambda v, f: ["-C", str(f), "push",
                   "git@github.com:innovation-upstream/devrc.git",
                   "HEAD:refs/heads/main"]),
]


@pytest.mark.parametrize("name,mode,argv", AUDIT_BYPASSES,
                         ids=[n for n, _, _ in AUDIT_BYPASSES])
def test_the_audited_bypasses_are_closed(guarded, name, mode, argv):
    before = _victim_state(guarded.victim)
    env = dict(guarded.env)
    if mode == "refuse-env":
        env.update({
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "alias.y",
            "GIT_CONFIG_VALUE_0":
                f"!git -C {guarded.victim} commit --allow-empty -m PWNED"})
    if "SSH" in name:
        env["GIT_SSH_COMMAND"] = (
            f"sh -c 'git -C {guarded.victim} commit --allow-empty -m PWNED' --")
    p = subprocess.run(["git", *argv(guarded.victim, guarded.fixture)],
                       env=env, input="payload\n", capture_output=True,
                       text=True, timeout=120)
    out = p.stdout + p.stderr
    assert p.returncode == norepo.REFUSED_EXIT, f"{name} was NOT refused:\n{out}"
    assert "GUARD 9" in out, f"{name} failed, but not by this guard:\n{out}"
    assert _victim_state(guarded.victim) == before, f"{name}: the victim CHANGED"


def test_status_is_forwarded_WITHOUT_rewriting_the_protected_index(guarded):
    """🔴 A "read" that WRITES. MEASURED: `git status` on a protected repo
    rewrote `.git/index` (bytes and mtime both moved) while sitting on the
    read-only fast path.

    Refusing it would be a permanently-red gate — this suite runs `git status`
    against the tree under test constantly — and the write is a stat-cache
    refresh that cannot lose data. So the shim forwards it with
    `--no-optional-locks`, which git provides for exactly this. Both halves are
    asserted: the command still WORKS, and the index does not move.
    """
    idx = guarded.victim / ".git" / "index"
    before = idx.read_bytes()
    os.utime(guarded.victim / "f", (0, 0))   # make the cached stat data stale
    p = subprocess.run(["git", "-C", str(guarded.victim), "status", "--porcelain"],
                       env=guarded.env, capture_output=True, text=True)
    assert p.returncode == 0, f"status was refused:\n{p.stdout}{p.stderr}"
    assert idx.read_bytes() == before, (
        "`git status` rewrote the protected repo's index; the "
        "--no-optional-locks forward is not being applied")


def test_status_on_an_UNPROTECTED_repo_is_untouched(guarded):
    """The other direction: fixtures must keep git's ordinary behaviour."""
    p = subprocess.run(["git", "-C", str(guarded.fixture), "status", "--porcelain"],
                       env=guarded.env, capture_output=True, text=True)
    assert p.returncode == 0, f"{p.stdout}{p.stderr}"


def test_a_c_option_aimed_at_the_fixture_ITSELF_is_still_allowed(guarded):
    """🔴 The positive control for the `-c` scanner. `-c` is normal git usage;
    refusing all of it would be a permanently-red gate. Only values aimed at a
    protected path, a network remote, or a shell-escape alias are refused."""
    for args in (["-c", "user.email=t@t", "commit", "--allow-empty", "-m", "ok"],
                 ["-c", "core.hooksPath=/tmp/harmless-hooks", "status", "--porcelain"],
                 ["-c", f"remote.origin.url={guarded.bare}", "remote", "-v"]):
        p = subprocess.run(["git", "-C", str(guarded.fixture), *args],
                           env=guarded.env, capture_output=True, text=True)
        assert p.returncode == 0, f"`-c` misuse refused a legitimate call {args}:\n{p.stdout}{p.stderr}"


def test_a_NON_PATH_operand_is_not_mistaken_for_a_protected_path(guarded):
    """🔴 THE PERMANENTLY-RED-GATE REGRESSION, and it is not hypothetical.

    `readlink -f` resolves a RELATIVE string against the shim's own cwd — which
    under `run-tests.sh` IS the protected repo. An earlier revision therefore
    treated ordinary WORDS as protected paths: `worktree list` flagged `list`,
    `worktree add -b topic <tmp>` flagged `topic`, and `-c user.email=t@t`
    flagged `t@t`. MEASURED: 15 tests failed on a correct tree.

    Run from INSIDE the protected repo, which is the condition that triggers it.
    """
    cases = [
        ["worktree", "list"],
        ["config", "--get", "core.bare"],
        ["-c", "user.email=t@t", "log", "--oneline", "-1"],
        ["-c", "core.abbrev=12", "rev-parse", "HEAD"],
        ["branch", "--show-current"],
    ]
    for args in cases:
        p = subprocess.run(["git", *args], cwd=str(guarded.victim),
                           env=guarded.env, capture_output=True, text=True)
        assert p.returncode == 0, (
            f"a non-path operand in `git {' '.join(args)}` was mistaken for a "
            f"protected path — this is a permanently-red gate:\n{p.stdout}{p.stderr}")


def test_a_config_write_FROM_A_WORKTREE_cannot_reach_the_base_clone(guarded, tmp_path):
    """🔴 THE MECHANISM THAT RE-ARMED `core.hooksPath` ON THE OPERATOR'S CLONE.

    A worktree shares the common git dir, so a plain `git config` inside one
    lands on the BASE CLONE's `.git/config`. MEASURED, both arms:

        UNGUARDED  git -C <worktree> config core.hooksPath /tmp/PWNED
                     -> rc 0, the BASE clone's config changed, hooksPath set
        UNGUARDED  git -C <worktree> config --worktree core.hooksPath X
                     -> rc 128 "fatal: --worktree cannot be used ... unless the
                        config extension worktreeConfig is enabled"
        GUARDED    -> rc 99, refused, base config byte-unchanged

    🔴 The correctly-scoped `--worktree` form FAILS BY DEFAULT, so the
    shared-write behaviour is not a mistake someone makes — it is the only form
    that works without prior setup. That is why this needs a structural guard
    rather than a convention.

    ⚠ SCOPE, stated honestly: this closes the TEST-SIDE. It does not stop an
    agent or a human running `git config` from a worktree by hand, and the
    observed instance came from an agent's tooling, not from a test.
    """
    base = _mkrepo(tmp_path / "wtbase")
    wt = tmp_path / "wtlinked"
    subprocess.run(["git", "-C", str(base), "worktree", "add", "-q", "-b",
                    "topic", str(wt)], check=True, capture_output=True)
    basecfg = base / ".git" / "config"

    # --- arm 1: UNGUARDED — the write must actually reach the BASE config -----
    unguarded = dict(os.environ)
    unguarded.pop("GIT_DIR", None)
    unguarded["GIT_CONFIG_GLOBAL"] = str(tmp_path / "gc-unguarded")
    before = basecfg.read_text()
    subprocess.run(["git", "-C", str(wt), "config", "core.hooksPath", "/tmp/PWNED"],
                   env=unguarded, capture_output=True, text=True)
    assert basecfg.read_text() != before, (
        "a config write from a worktree no longer reaches the base clone — if "
        "git changed this, say so; do not delete the guard.")

    # --- arm 2: GUARDED — refused, base config untouched ----------------------
    base2 = _mkrepo(tmp_path / "wtbase2")
    wt2 = tmp_path / "wtlinked2"
    subprocess.run(["git", "-C", str(base2), "worktree", "add", "-q", "-b",
                    "topic", str(wt2)], check=True, capture_output=True)
    prot = norepo.protected_paths(base2)
    assert str(wt2.resolve()) in prot, (
        f"the linked worktree is not in the protected set {prot}; "
        "protected_paths must enumerate `git worktree list`")
    gd = tmp_path / "guard-wt"
    norepo.install(gd, prot, [str(guarded.gitconfig)])
    genv = dict(os.environ)
    genv.pop("GIT_DIR", None)
    genv["PATH"] = f"{gd}{os.pathsep}" + os.environ["PATH"]
    genv["GIT_CONFIG_GLOBAL"] = str(guarded.gitconfig)
    basecfg2 = base2 / ".git" / "config"
    b2 = basecfg2.read_text()
    p = subprocess.run(["git", "-C", str(wt2), "config", "core.hooksPath", "/tmp/PWNED"],
                       env=genv, capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert p.returncode == norepo.REFUSED_EXIT, f"NOT refused:\n{out}"
    assert "GUARD 9" in out, out
    assert basecfg2.read_text() == b2, "refused, but the base config CHANGED"


# --------------------------------------------------------------------------- #
# 3e. THE OUTSIDE REVIEW'S F1-F6
#
# 🔴 Each was MEASURED to land at rc 0 with LEDGER DELTA 0 against the revision
# that had already passed every battery above — i.e. the per-target table would
# have printed `refused=0` for them. Each carries its own live-axis arm, so a
# vector that stops being dangerous fails loudly instead of reassuring.
#
# The shared root, and the reason these are grouped: the guard asked git "which
# repo am I in" and then reasoned over enumerated shapes. F1 is a write whose
# REPO is innocent and whose DESTINATION is not; F2 is a URL one character
# outside two globs; F3/F4 hand a program to the real binary, which never sees
# this shim because git prepends its own libexec to PATH.
# --------------------------------------------------------------------------- #
def _rev689_victim(tmp_path, name):
    v = _mkrepo(tmp_path / name)
    bare = _mkrepo(tmp_path / f"{name}-origin.git", bare=True)
    subprocess.run(["git", "-C", str(v), "remote", "add", "origin", str(bare)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(v), "push", "-q", "origin", "main"],
                   check=True, capture_output=True)
    return v, bare


def _rev689_state(v, bare):
    def q(*a):
        return _git("-C", str(v), *a).stdout.strip()
    return {
        "config": q("config", "--local", "--list"),
        "branches": q("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"),
        "HEAD": q("symbolic-ref", "-q", "HEAD"),
        "commits": q("rev-list", "--count", "--all"),
        "worktree": _wt_digest(v),
        # 🔴 DIRECTORY-AWARE. `git init <victim>/planted` creates ONLY
        # `planted/.git/**`, which a file digest that skips `.git` cannot see —
        # the vector looked harmless because the PROBE was blind, not because
        # nothing happened.
        "tree-entries": "|".join(sorted(
            str(p.relative_to(v)) for p in v.iterdir() if p.name != ".git")),
        "bare-refs": _git("-C", str(bare), "for-each-ref",
                          "--format=%(refname) %(objectname)").stdout.strip(),
    }


def _rev689_case(tag, v, bare, fixture, tmp_path):
    """(argv, env-additions) for `tag`, aimed at this victim."""
    if tag == "F1-push-worktree":
        return ["-C", str(fixture), "push", str(v), "+HEAD:refs/heads/main"], {}
    if tag == "F1-push-bare":
        return ["-C", str(fixture), "push", str(bare), "+HEAD:refs/heads/main"], {}
    if tag == "F2-userless-scp":
        return ["-C", str(fixture), "push",
                "github.com:innovation-upstream/devrc.git", "HEAD:refs/heads/main"], {}
    if tag in ("F3-hookspath", "F3-hookspath-env"):
        hooks = tmp_path / f"evilhooks-{tag}"
        hooks.mkdir(exist_ok=True)
        write_exec(hooks / "post-commit",
                   f'git -C "{v}" config core.bare true\n'
                   f'git -C "{v}" branch -m main trunk\n'
                   f'git -C "{v}" config remote.origin.url /tmp/PWNED.git\n'
                   f'touch "{v}/PLANTED"\n')
        if tag == "F3-hookspath":
            return ["-C", str(fixture), "-c", f"core.hooksPath={hooks}",
                    "commit", "--allow-empty", "-m", "x"], {}
        return (["-C", str(fixture), "commit", "--allow-empty", "-m", "x"],
                {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                 "GIT_CONFIG_VALUE_0": str(hooks)})
    if tag == "F4-diff-external":
        scr = tmp_path / f"extdiff-{tag}.sh"
        write_exec(scr, f'touch "{v}/PLANTED-DIFF"\nexit 0\n')
        # A TRACKED file: `git diff` shows nothing for an untracked one, so
        # the external differ would never be invoked and the vector would
        # look harmless.
        (fixture / "f").write_text("CHANGED\n")
        return ["-C", str(fixture), "-c", f"diff.external={scr}", "diff"], {}
    if tag == "F6-init":
        return ["init", "-q", str(v / "planted-init")], {}
    if tag == "F6-clone":
        return ["clone", "-q", str(fixture), str(v / "planted-clone")], {}
    # 🔴 THE ABSENT-OPERAND-REPORTS-INNOCENT SHAPE. Both `cd`s fail, so a
    # resolver that returns the RAW string prefix-matches an allowed directory
    # and lets the write through — the same family as `git diff --quiet`
    # reporting SAME for a path on neither side. MEASURED: `readlink -m`
    # normalises `<allowed>/does-not-exist/deeper/../../../victim/planted` to
    # `<victim>/planted`, so the traversal is caught by the same fix as F6.
    if tag == "F6-traversal-init":
        allowed = tmp_path / f"allowed-{tag}"
        allowed.mkdir(exist_ok=True)
        return ["init", "-q",
                f"{allowed}/does-not-exist/deeper/../../../{v.name}/planted-trav"], {}
    if tag == "F6-traversal-clone":
        allowed = tmp_path / f"allowed-{tag}"
        allowed.mkdir(exist_ok=True)
        return ["clone", "-q", str(fixture),
                f"{allowed}/does-not-exist/deeper/../../../{v.name}/planted-trav2"], {}
    raise AssertionError(tag)


#: ⚠ `F1-push-worktree` is deliberately ABSENT: MEASURED, git's own
#: `receive.denyCurrentBranch` refuses a push to a checked-out branch
#: ("refusing to update checked out branch"), so on this git it is NOT a live
#: vector and a battery entry for it could only ever agree with itself.
#: `F1-push-bare` is the live form — and the more serious one, since the
#: public repo is a bare remote.
REV689_VECTORS = (
    "F1-push-bare", "F2-userless-scp",
    "F3-hookspath", "F3-hookspath-env", "F4-diff-external",
    "F6-init", "F6-clone", "F6-traversal-init", "F6-traversal-clone",
)


@pytest.mark.parametrize("tag", REV689_VECTORS)
def test_the_reviewed_vector_is_DANGEROUS_and_the_guard_HOLDS(guarded, tmp_path, tag):
    """🔴 BOTH ARMS. Arm 1 must damage an asserted axis with the guard OFF, or
    the vector was never dangerous; arm 2 must be refused by THIS guard with the
    victim byte-unchanged.

    🔴 `GIT_ALLOW_PROTOCOL` is REMOVED in both arms. The review's point about F2
    is that the transport lever — which this PR itself calls the weakest of the
    three and not the guard — is what stopped it, and 229 in-tree `env={...}`
    sites would drop that lever. Crediting the guard for it would be measuring
    the wrong thing.
    """
    # --- arm 1: UNGUARDED --------------------------------------------------
    v1, b1 = _rev689_victim(tmp_path, f"victim-u-{tag}")
    argv, envadd = _rev689_case(tag, v1, b1, guarded.fixture, tmp_path)
    ue = _unguarded_env(guarded.dir)
    _assert_truly_unguarded(ue, tmp_path, f"reviewed-{tag}")
    ue["GIT_CONFIG_GLOBAL"] = str(tmp_path / f"gc-u-{tag}")
    ue.update(envadd)
    before = _rev689_state(v1, b1)
    u = subprocess.run(["git", *argv], env=ue, capture_output=True, text=True,
                       timeout=180)
    moved = [k for k in before if before[k] != _rev689_state(v1, b1)[k]]
    if tag == "F2-userless-scp":
        # A real host cannot be reached from the gate, so the damage axis here
        # is REACH, not victim state: git must have attempted the ssh
        # connection rather than been stopped by anything of ours.
        out = u.stdout + u.stderr
        assert "github.com" in out and "GUARD 9" not in out, (
            f"unguarded, the userless-scp push was not even attempted:\n{out}")
    else:
        assert moved, (
            f"{tag}: UNGUARDED this moved NO asserted axis (rc={u.returncode}); "
            f"the vector is harmless and refusing it proves nothing.\n"
            f"{u.stdout}{u.stderr}")

    # --- arm 2: GUARDED ----------------------------------------------------
    v2, b2 = _rev689_victim(tmp_path, f"victim-g-{tag}")
    argv2, envadd2 = _rev689_case(tag, v2, b2, guarded.fixture, tmp_path)
    gd = tmp_path / f"guard-{tag}"
    # 🔴 ONLY THE VICTIM IS PASSED. Its local bare origin must be protected
    # AUTOMATICALLY by `protected_paths`, or F1b is closed only when the caller
    # already knew to pass it. MEASURED: passing both made a mutant that
    # deleted the automatic protection SURVIVE — the test was supplying the
    # very thing it was meant to be checking.
    norepo.install(gd, norepo.protected_paths(v2), [str(guarded.gitconfig)])
    ge = dict(os.environ)
    ge.pop("GIT_DIR", None)
    ge.pop("GIT_ALLOW_PROTOCOL", None)
    ge["PATH"] = f"{gd}{os.pathsep}" + os.environ["PATH"]
    ge["GIT_CONFIG_GLOBAL"] = str(guarded.gitconfig)
    ge.update(envadd2)
    b = _rev689_state(v2, b2)
    p = subprocess.run(["git", *argv2], env=ge, capture_output=True, text=True,
                       timeout=180)
    out = p.stdout + p.stderr
    assert p.returncode == norepo.REFUSED_EXIT, (
        f"{tag}: expected the guard's exit {norepo.REFUSED_EXIT}, got "
        f"{p.returncode}\n{out}")
    assert "GUARD 9" in out, f"{tag}: refused, but not by this guard:\n{out}"
    a = _rev689_state(v2, b2)
    assert a == b, f"{tag}: refused but state moved: {[k for k in b if b[k] != a[k]]}"


def test_the_injectable_key_list_is_an_ALLOWLIST_not_a_denylist():
    """🔴 F3's framing, pinned in the CODE. A denylist fails on the next key
    nobody thought of; the shim must refuse an unknown key by default."""
    shim = norepo.shim_body("/usr/bin/git", Path("/tmp/l"), Path("/tmp/p"),
                            Path("/tmp/c"))
    assert "is not on the injectable-config allowlist" in shim
    # ...and an obviously-executable key must NOT be on it.
    for k in ("core.fsmonitor", "diff.external", "core.pager", "core.editor",
              "include.path", "alias.x", "sequence.editor"):
        assert k not in norepo.INJECTABLE_KEYS, (
            f"{k} names a program or a config to load; it must not be injectable")


def test_a_neutralising_hookdir_is_allowed_but_an_ARMED_one_is_not(guarded, tmp_path):
    """🔴 The distinction is MEASURED, not spelled. `analyze-service-index/
    commit.sh` sets `core.hooksPath` at an EMPTY dir on purpose to NEUTRALISE
    hooks, and its suite drives that path — refusing the key outright would be
    a permanently-red gate. What is refused is a directory holding an
    executable, which is the measured full-incident vector."""
    empty = tmp_path / "empty-hooks"
    empty.mkdir()
    p = subprocess.run(["git", "-C", str(guarded.fixture), "-c",
                        f"core.hooksPath={empty}", "commit", "--allow-empty",
                        "-m", "neutralised"], env=guarded.env,
                       capture_output=True, text=True)
    assert p.returncode == 0, (
        f"neutralising hooks was refused — permanently-red gate:\n{p.stdout}{p.stderr}")

    armed = tmp_path / "armed-hooks"
    armed.mkdir()
    write_exec(armed / "post-commit", "true\n")
    q = subprocess.run(["git", "-C", str(guarded.fixture), "-c",
                        f"core.hooksPath={armed}", "commit", "--allow-empty",
                        "-m", "armed"], env=guarded.env,
                       capture_output=True, text=True)
    assert q.returncode == norepo.REFUSED_EXIT, (
        f"an ARMED hook directory was allowed:\n{q.stdout}{q.stderr}")


def test_the_scanners_run_ABOVE_the_read_fast_path():
    """🔴 F4, the structural root. With the fast path first, any subcommand on
    the read list carried its injections straight to the real binary."""
    src = (REPO_ROOT / "scripts" / "testlib" / "norepo.py").read_text()
    body = src[src.index("def shim_body("):src.index("def install(")]
    fast = body.index("---- FAST PATH")
    for marker in ("_scan_cval", "THE ENVIRONMENT VECTOR", "GIT_CONFIG_COUNT"):
        assert body.index(marker) < fast, (
            f"the read fast path precedes `{marker}`; a read subcommand would "
            "carry its injections to the real binary unscanned")


def test_the_protected_config_set_is_RESOLVED_not_hardcoded(tmp_path):
    """🔴 F5. `~/.gitconfig` does not exist on this host — git uses
    `${XDG_CONFIG_HOME:-~/.config}/git/config`. Hardcoding the former protected
    a file that is never written."""
    guard = tmp_path / "g"
    repo = _mkrepo(tmp_path / "r")
    assert norepo.main([str(guard), str(repo)]) == 0
    listed = (guard / norepo.PROTECTED_CONFIG_NAME).read_text().splitlines()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    assert str(xdg / "git" / "config") in listed, (
        f"git's actual --global location is not protected. Listed: {listed}")
    assert str(Path.home() / ".gitconfig") in listed, "the other candidate is unguarded"


def test_a_NOT_YET_EXISTING_path_behind_a_SYMLINK_still_resolves(guarded, tmp_path):
    """🔴 The case that separates `readlink -m` from `readlink -f`.

    For a plain absolute path the two agree by accident: `-f` fails on a
    non-existent leaf, the `|| echo "$1"` fallback hands back the raw string,
    and the raw string still has the protected prefix. MEASURED — a mutant
    reverting `-m` to `-f` SURVIVED the F6 vectors for exactly that reason.

    It stops agreeing when an ANCESTOR is a symlink: `-f` cannot canonicalise
    the leaf, the raw string keeps the symlink spelling, and it no longer
    matches the resolved protected path. `-m` canonicalises regardless of
    existence, which is what makes the destination check hold under a symlinked
    path — the shape `~/workspace` genuinely has on this host.
    """
    real = tmp_path / "real-parent"
    real.mkdir()
    victim = _mkrepo(real / "victim")
    link = tmp_path / "linked-parent"
    link.symlink_to(real)

    gd = tmp_path / "guard-symlink"
    norepo.install(gd, norepo.protected_paths(victim), [str(guarded.gitconfig)])
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env["PATH"] = f"{gd}{os.pathsep}" + os.environ["PATH"]
    env["GIT_CONFIG_GLOBAL"] = str(guarded.gitconfig)

    # 🔴 TWO missing components, not one. MEASURED: `readlink -f` canonicalises
    # fine when only the LAST component is missing, so a one-level destination
    # made `-f` and `-m` agree and a mutant reverting `-m` SURVIVED. It fails —
    # returning empty, so the raw symlink spelling is kept and no longer
    # matches the resolved protected path — only when more than the last
    # component is absent.
    dest = link / "victim" / "planted" / "through-symlink"
    p = subprocess.run(["git", "init", "-q", str(dest)], env=env,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert p.returncode == norepo.REFUSED_EXIT, (
        f"a not-yet-existing path behind a symlink was NOT refused:\n{out}")
    assert "GUARD 9" in out, out
    assert not dest.exists(), "refused, but the repository was still created"


def _farm_env(guard_dir, gitconfig):
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env["PATH"] = f"{guard_dir}{os.pathsep}" + os.environ["PATH"]
    env["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    return env


def test_an_alias_in_a_repos_OWN_config_is_guarded_by_the_exec_farm(guarded, tmp_path):
    """🔴 THE RESIDUAL THIS MODULE USED TO ONLY DOCUMENT, NOW CLOSED.

    Git PREPENDS `libexec/git-core` to PATH for every process it spawns, so a
    bare `git` inside a `!`-alias resolves to the REAL binary and never reaches
    a PATH shim. Scanning the current invocation cannot help when the alias is
    ALREADY IN the repo's own `.git/config`.

    BOTH ARMS, because the point is that one layer is insufficient:
      without GIT_EXEC_PATH -> the alias lands a commit on the victim
      with the farm         -> refused by THIS guard, victim unchanged
    """
    victim = _mkrepo(tmp_path / "farm-victim")
    fixture = _mkrepo(tmp_path / "farm-fixture")
    subprocess.run(["git", "-C", str(fixture), "config", "alias.pwn",
                    f"!git -C {victim} commit --allow-empty -m PWNED-OWN-ALIAS"],
                   check=True, capture_output=True)
    gd = tmp_path / "farm-guard"
    norepo.install(gd, norepo.protected_paths(victim), [str(guarded.gitconfig)])

    def commits():
        return _git("-C", str(victim), "rev-list", "--count", "--all").stdout.strip()

    # --- arm 1: PATH shim only. The residual must be REAL. -------------------
    base = commits()
    env = _farm_env(gd, guarded.gitconfig)
    env.pop("GIT_EXEC_PATH", None)
    subprocess.run(["git", "-C", str(fixture), "pwn"], env=env,
                   capture_output=True, text=True, timeout=120)
    assert commits() != base, (
        "the alias-in-own-config residual no longer reproduces. If git stopped "
        "prepending its libexec, say so — do not delete the farm.")

    # --- arm 2: with the farm. ----------------------------------------------
    victim2 = _mkrepo(tmp_path / "farm-victim2")
    fixture2 = _mkrepo(tmp_path / "farm-fixture2")
    subprocess.run(["git", "-C", str(fixture2), "config", "alias.pwn",
                    f"!git -C {victim2} commit --allow-empty -m PWNED-OWN-ALIAS"],
                   check=True, capture_output=True)
    gd2 = tmp_path / "farm-guard2"
    norepo.install(gd2, norepo.protected_paths(victim2), [str(guarded.gitconfig)])
    farm = norepo.install_exec_farm(gd2)
    assert farm, "the exec farm could not be built"
    env2 = _farm_env(gd2, guarded.gitconfig)
    env2["GIT_EXEC_PATH"] = farm

    def commits2():
        return _git("-C", str(victim2), "rev-list", "--count", "--all").stdout.strip()

    base2 = commits2()
    p = subprocess.run(["git", "-C", str(fixture2), "pwn"], env=env2,
                       capture_output=True, text=True, timeout=120)
    out = p.stdout + p.stderr
    assert commits2() == base2, (
        f"the farm did not stop the alias; victim moved {base2} -> {commits2()}\n{out}")
    assert "GUARD 9" in out, f"stopped, but not by this guard:\n{out}"


def test_the_exec_farm_does_not_break_ordinary_git(guarded, tmp_path):
    """🔴 A farm that broke git would be a permanently-red gate. Substituting
    `git` inside libexec must leave every other helper reachable."""
    gd = tmp_path / "ok-guard"
    norepo.install(gd, norepo.protected_paths(guarded.victim), [str(guarded.gitconfig)])
    farm = norepo.install_exec_farm(gd)
    assert farm
    env = _farm_env(gd, guarded.gitconfig)
    env["GIT_EXEC_PATH"] = farm
    for label, argv in (("status", ["-C", str(guarded.fixture), "status", "--porcelain"]),
                        ("log", ["-C", str(guarded.fixture), "log", "--oneline", "-1"]),
                        ("commit", ["-C", str(guarded.fixture), "commit", "--allow-empty", "-m", "ok"]),
                        ("diff", ["-C", str(guarded.fixture), "diff"]),
                        ("rebase --help", ["rebase", "--help"])):
        r = subprocess.run(["git", *argv], env=env, capture_output=True, text=True)
        assert r.returncode == 0, f"the farm broke `git {label}`:\n{r.stdout}{r.stderr}"
    # The farm must carry essentially all of git's helpers, not a token few.
    assert len(list(Path(farm).iterdir())) > 50, "the farm is suspiciously small"


#: 🔴 THE ESCAPE SET, DERIVED BY MEASUREMENT, PINNED BOTH WAYS.
#: An earlier revision asserted the residual was ONE — the shell case — and
#: omitted the absolute-path case entirely. Six shapes were measured; the
#: guard's reach is exactly the four that go through PATH resolution.
ALIAS_SHAPES = {
    "bare git": ("!git -C {v} commit --allow-empty -m PWN", "guarded"),
    "git via sh -c": ("!sh -c 'git -C {v} commit --allow-empty -m PWN'", "guarded"),
    "git via env": ("!env git -C {v} commit --allow-empty -m PWN", "guarded"),
    "git via command": ("!command git -C {v} commit --allow-empty -m PWN", "guarded"),
    "absolute git path": ("!{git} -C {v} commit --allow-empty -m PWN", "escapes"),
    "no git at all": ("!touch {v}/PLANTED-SHELL", "escapes"),
}


@pytest.mark.parametrize("shape", sorted(ALIAS_SHAPES))
def test_the_farms_reach_is_exactly_the_derived_set(guarded, tmp_path, shape):
    """🔴 BOTH DIRECTIONS. A shape marked `guarded` must be refused; a shape
    marked `escapes` must STILL escape.

    The second half matters as much as the first: if an escape silently stops
    escaping, the module's stated residual is now too pessimistic and should be
    corrected with a measurement — and if a guarded shape starts escaping, the
    farm has regressed. Either way this test says which.
    """
    body, expect = ALIAS_SHAPES[shape]
    victim = _mkrepo(tmp_path / f"as-v-{abs(hash(shape)) % 9999}")
    fixture = _mkrepo(tmp_path / f"as-f-{abs(hash(shape)) % 9999}")
    real = shutil.which("git")
    subprocess.run(["git", "-C", str(fixture), "config", "alias.pwn",
                    body.format(v=victim, git=real)], check=True, capture_output=True)
    gd = tmp_path / f"as-g-{abs(hash(shape)) % 9999}"
    norepo.install(gd, norepo.protected_paths(victim), [str(guarded.gitconfig)])
    farm = norepo.install_exec_farm(gd)
    assert farm, "the exec farm could not be built"
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env["PATH"] = f"{gd}{os.pathsep}" + os.environ["PATH"]
    env["GIT_CONFIG_GLOBAL"] = str(guarded.gitconfig)
    env["GIT_EXEC_PATH"] = farm

    def moved():
        n = _git("-C", str(victim), "rev-list", "--count", "--all").stdout.strip()
        return n != "1" or (victim / "PLANTED-SHELL").exists()

    p = subprocess.run(["git", "-C", str(fixture), "pwn"], env=env,
                       capture_output=True, text=True, timeout=120)
    out = p.stdout + p.stderr
    if expect == "guarded":
        assert not moved(), f"{shape} was expected GUARDED but it landed:\n{out}"
        assert "GUARD 9" in out, f"{shape} was stopped, but not by this guard:\n{out}"
    else:
        assert moved(), (
            f"{shape} was expected to ESCAPE and did not. If the farm now covers "
            "it, the module's stated residual is too pessimistic — correct it "
            f"with the measurement rather than leaving a stale claim.\n{out}")


def test_the_exec_farm_is_DERIVED_and_verified_as_a_SET(guarded, tmp_path):
    """🔴 A COUNT TEST PASSES WHEN ONE HELPER IS SWAPPED FOR ANOTHER.

    The farm is rebuilt from `git --exec-path` on every call, so a helper added
    by a git upgrade is picked up automatically — it is not a snapshot ledger.
    This asserts the SET, and that our own `git` is a real copy rather than a
    link to the binary it is meant to shadow.
    """
    gd = tmp_path / "setfarm-guard"
    norepo.install(gd, norepo.protected_paths(guarded.victim), [str(guarded.gitconfig)])
    farm = Path(norepo.install_exec_farm(gd))
    src = Path(subprocess.run([norepo.real_git(), "--exec-path"],
                              capture_output=True, text=True).stdout.strip())
    assert set(os.listdir(farm)) == set(os.listdir(src)), (
        "the farm's entry SET differs from git's exec-path; a helper git looks "
        "for would reach the real binary unguarded")
    assert not (farm / "git").is_symlink(), "the farm's `git` must be OUR shim"


def test_a_farm_that_cannot_be_BUILT_makes_install_REFUSE(guarded, tmp_path):
    """🔴 FAIL CLOSED when the farm cannot be completed.

    MEASURED on an earlier revision: a pre-existing regular file at
    `.git-archimport-wrapped` was `continue`d past and the farm was returned as
    usable anyway — a hole handed to git silently. The build now starts from a
    clean directory, so a stale blocker cannot survive; what remains is the
    case where the directory cannot be written at all, and that must refuse
    rather than return a partial farm.
    """
    gd = tmp_path / "closed-guard"
    norepo.install(gd, norepo.protected_paths(guarded.victim), [str(guarded.gitconfig)])
    # Sanity: it builds normally here, so a refusal below is about the block.
    assert norepo.install_exec_farm(gd), "the farm does not build even unblocked"
    shutil.rmtree(gd / norepo.EXEC_FARM_NAME, ignore_errors=True)
    gd.chmod(0o500)                       # read+execute, not writable
    try:
        res = norepo.install_exec_farm(gd)
    finally:
        gd.chmod(0o700)
    assert res == "", (
        "a farm that could not be built did not refuse; a partial exec-path "
        "would be handed to git with a hole in it")


def test_the_runner_treats_an_unusable_farm_as_FATAL():
    """Proceeding with a known hole is the thing being refused."""
    text = RUN_TESTS.read_text(encoding="utf-8")
    i = text.index("GITGUARD_FARM=")
    tail = text[i:i + 2000]
    assert "FATAL" in tail and "exit 2" in tail, (
        "run-tests.sh no longer refuses to run when the exec farm is unusable")


def test_the_runner_exports_the_exec_farm():
    text = RUN_TESTS.read_text(encoding="utf-8")
    assert 'export GIT_EXEC_PATH="$GITGUARD_FARM"' in text, (
        "run-tests.sh does not export the exec farm; a git-spawned child would "
        "resolve `git` to the real binary")
    assert "exec-farm=" in (REPO_ROOT / "scripts/testlib/norepo.py").read_text()


def test_the_integrity_check_FAILS_on_a_DELIBERATELY_GUARDED_arm(guarded, tmp_path):
    """🔴 THE CHECK BUILT TO PREVENT A FAKE GREEN, CHECKED ITSELF.

    Every VACUOUS verdict in this file rests on `_assert_truly_unguarded`
    having passed. If it can pass on a GUARDED arm, every one of them is
    inherited from a broken instrument. So: hand it an arm that is guarded on
    purpose and require it to raise.
    """
    with pytest.raises(AssertionError):
        _assert_truly_unguarded(dict(guarded.env), tmp_path, "deliberately-guarded",
                                protected_hint=guarded.victim)


def test_the_positive_canary_ALONE_discriminates(guarded, tmp_path):
    """🔴 LEG 3 IN ISOLATION. MEASURED on its predecessor: a commit inside an
    unprotected fixture passed with the guard loaded AND absent — it could not
    distinguish the two states, so its success proved permission.

    This asserts the pair directly: refused under the guard, rc 0 without it.
    """
    guarded_r = _discriminating_canary(dict(guarded.env), guarded.victim, "g")
    assert guarded_r.returncode == norepo.REFUSED_EXIT, (
        "the positive canary is an operation the guard PERMITS; its success "
        "would be evidence of permission, not of the guard being absent:\n"
        f"{guarded_r.stdout}{guarded_r.stderr}")
    unguarded_r = _discriminating_canary(
        _unguarded_env(guarded.dir), guarded.victim, "u")
    assert unguarded_r.returncode == 0, (
        f"the canary does not succeed even unguarded, so it cannot be used as "
        f"evidence of absence:\n{unguarded_r.stdout}{unguarded_r.stderr}")


def test_farm_is_complete_rejects_a_missing_or_shadowed_entry(tmp_path):
    """🔴 The set check, tested DIRECTLY because it is unreachable inside
    `install_exec_farm` on a healthy box — the build starts clean and always
    produces the right set. An unreachable guard is one nobody has watched
    work."""
    farm = tmp_path / "farm"
    farm.mkdir()
    for n in ("a", "b"):
        (farm / n).write_text("x")
    (farm / "git").write_text("shim")
    assert norepo.farm_is_complete(farm, {"a", "b", "git"})
    # a MISSING helper
    assert not norepo.farm_is_complete(farm, {"a", "b", "git", "c"})
    # a SWAPPED helper: same count, different set
    assert not norepo.farm_is_complete(farm, {"a", "zzz", "git"})
    # `git` that merely LINKS to the real binary guards nothing
    (farm / "git").unlink()
    (farm / "git").symlink_to(shutil.which("git") or "/bin/true")
    assert not norepo.farm_is_complete(farm, {"a", "b", "git"})


# --------------------------------------------------------------------------- #
# 3f. THE MITIGATION'S OWN ATTACK SURFACE, AND THE DISPATCH-NAME FARM
# --------------------------------------------------------------------------- #
def test_the_shim_NEVER_shell_interprets_an_argv_derived_string(guarded, tmp_path):
    """🔴 A GUARD THAT IS WORSE THAN NO GUARD.

    `--config-env=key=VAR` puts VAR on the command line. An earlier revision did
    `eval "_cv=${$_ev:-}"` with it, so a command substitution in the NAME ran
    inside the shim, before any check, on a pure READ. MEASURED:

        git --config-env=core.pager='V:-$(touch X)' --version
          this shim -> X CREATED, then exit 99
          real git  -> exit 128, "fatal: missing environment variable", nothing run

    Real git rejects the name and executes nothing, so the mitigation was
    introducing execution the unguarded system refuses. Both halves are
    asserted: nothing runs, AND the invocation is refused.
    """
    marker = tmp_path / "PWNED-BY-SHIM"
    p = subprocess.run(
        ["git", f"--config-env=core.pager=V:-$(touch {marker})", "--version"],
        env=guarded.env, capture_output=True, text=True, timeout=60)
    assert not marker.exists(), (
        "the shim EXECUTED a command taken from argv. On this path the guard is "
        "strictly worse than no guard — real git refuses the name and runs "
        f"nothing.\n{p.stdout}{p.stderr}")
    assert p.returncode == norepo.REFUSED_EXIT, (
        f"the injection did not run but was not refused either:\n{p.stdout}{p.stderr}")


def test_a_VALID_config_env_name_still_works(guarded):
    """The other direction: `--config-env` with an ordinary name must not be
    refused, or the fix is a permanently-red gate."""
    env = dict(guarded.env)
    env["G9_VALID_NAME"] = "someone@example.com"
    p = subprocess.run(["git", "-C", str(guarded.fixture),
                        "--config-env=user.email=G9_VALID_NAME",
                        "config", "--get", "user.email"],
                       env=env, capture_output=True, text=True)
    assert p.returncode == 0, f"a valid --config-env was refused:\n{p.stdout}{p.stderr}"


def test_the_eval_family_is_audited_not_just_the_one_instance():
    r"""🔴 THE PATTERN, NOT THE INSTANCE.

    Every `eval` in the rendered shim must take a HARDCODED variable name or a
    digit-validated counter -- never a value from argv. Anything that
    shell-interprets an argv-derived string belongs to the same family as the
    `--config-env` RCE by construction, so the audit is over the whole set.

    🔴 CODE ONLY. The first revision of this test searched every line and
    matched the COMMENT documenting the bug -- the prose-walkable shape that
    has now caught three separate structural checks in this file.
    """
    raw = norepo.shim_body("/usr/bin/git", Path("/tmp/l"), Path("/tmp/p"),
                           Path("/tmp/c"))
    # Strip comments ONCE, up front: every assertion below is about code.
    shim = "\n".join(ln for ln in raw.splitlines()
                     if not ln.strip().startswith("#"))
    evals = [ln.strip() for ln in shim.splitlines() if "eval " in ln]
    # $_v comes from REFUSED_ENV (hardcoded); $_n is the digit-validated
    # GIT_CONFIG_COUNT index. Neither can carry a value from the command line.
    allowed_names = ("$_v", "GIT_CONFIG_KEY_$_n", "GIT_CONFIG_VALUE_$_n")
    for e in evals:
        assert any(n in e for n in allowed_names), (
            "an `eval` in the shim takes an operand that is not a hardcoded "
            f"name or a validated counter — if it can come from argv that is "
            f"remote code execution inside the guard:\n  {e}")
    assert len(evals) == 3, (
        f"the shim's `eval` set changed ({len(evals)} found). Re-audit each "
        f"operand before widening this pin:\n" + "\n".join(evals))
    # ...and the argv-derived path must be read WITHOUT interpretation.
    assert "printenv" in shim, "the --config-env value is not read with printenv"
    assert 'eval "_cv=' not in shim, (
        "the --config-env value is being eval'd again")


FARM_DISPATCH_CASES = [
    ("git-config -f into a protected repo", "refuse",
     lambda farm, v, f: ([f"{farm}/git-config", "-f", str(v / ".git" / "config"),
                          "core.bale", "true"], None)),
    ("git-commit into a protected repo", "refuse",
     lambda farm, v, f: ([f"{farm}/git-commit", "--allow-empty", "-m", "PWN"], str(v))),
    ("git-status on a fixture", "allow",
     lambda farm, v, f: ([f"{farm}/git-status", "--porcelain"], str(f))),
    ("git-log on a PROTECTED repo (a read)", "allow",
     lambda farm, v, f: ([f"{farm}/git-log", "--oneline", "-1"], str(v))),
]


@pytest.mark.parametrize("label,expect,build", FARM_DISPATCH_CASES,
                         ids=[c[0] for c in FARM_DISPATCH_CASES])
def test_the_farm_routes_every_DISPATCH_NAME_not_just_git(guarded, tmp_path,
                                                          label, expect, build):
    """🔴 GIT DISPATCHES ON argv[0], AND THE FARM MUST OWN EVERY SUCH NAME.

    MEASURED against an earlier revision that substituted only the literal
    `git`: all 181 `git-<verb>` entries pointed at the real binary and sat
    FIRST on PATH in exactly the alias/hook context the farm exists for —

        <farm>/git-config -f <victim>/.git/config core.bale true
          -> core.bare false -> true, rc 0

    the incident's own signature, with `git` never spelled.

    🔴 ROUTED BY NAME, NEVER BY INODE. On this host those entries are SYMLINKS
    and none shares git's inode; on another packaging they are hardlinks. The
    property git uses is the NAME, so an inode/`stat` enumeration is the wrong
    attribute and would report a clean farm on one packaging while missing
    every entry on the other.
    """
    victim = _mkrepo(tmp_path / f"fd-v-{abs(hash(label)) % 9999}")
    fixture = _mkrepo(tmp_path / f"fd-f-{abs(hash(label)) % 9999}")
    gd = tmp_path / f"fd-g-{abs(hash(label)) % 9999}"
    norepo.install(gd, norepo.protected_paths(victim), [str(guarded.gitconfig)])
    farm = norepo.install_exec_farm(gd)
    assert farm
    argv, cwd = build(farm, victim, fixture)
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env["PATH"] = f"{gd}{os.pathsep}" + os.environ["PATH"]
    env["GIT_CONFIG_GLOBAL"] = str(guarded.gitconfig)
    env["GIT_EXEC_PATH"] = farm
    before = (victim / ".git" / "config").read_text()
    p = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True,
                       timeout=60)
    out = p.stdout + p.stderr
    if expect == "refuse":
        assert p.returncode == norepo.REFUSED_EXIT, f"{label} not refused:\n{out}"
        assert "GUARD 9" in out, out
        assert (victim / ".git" / "config").read_text() == before
    else:
        assert p.returncode == 0, (
            f"{label} was refused — a permanently-red gate; the shim must "
            f"dispatch correctly when invoked AS git-<verb>:\n{out}")


def test_every_git_dispatch_name_in_the_farm_is_OUR_shim(guarded, tmp_path):
    """Derived at run time and checked as a SET, by name."""
    gd = tmp_path / "dispatch-guard"
    norepo.install(gd, norepo.protected_paths(guarded.victim), [str(guarded.gitconfig)])
    farm = Path(norepo.install_exec_farm(gd))
    names = set(os.listdir(farm))
    dispatch = {n for n in names if n == "git" or n.startswith("git-")}
    assert len(dispatch) > 100, f"only {len(dispatch)} dispatch names — suspicious"
    unrouted = [n for n in dispatch
                if (farm / n).is_symlink() or not (farm / n).is_file()]
    assert not unrouted, (
        f"{len(unrouted)} dispatch names still reach the real binary: "
        f"{sorted(unrouted)[:8]}")


READ_PATH_CASES = [
    ("log --output= into a protected repo", "refuse",
     lambda v, f, tmp: ["-C", str(f), "log", "-p",
                        f"--output={v / '.git' / 'config'}"]),
    ("log --output= into an ALLOWED path", "allow",
     lambda v, f, tmp: ["-C", str(f), "log", f"--output={tmp / 'ok.txt'}"]),
    # 🔴 THE SPACE-SEPARATED FORM. MEASURED: with only `--output=<path>`
    # covered, a mutant that deleted the `--output <path>` branch SURVIVED —
    # the two spellings are handled by different branches and only one was
    # exercised. A synonym walks a guard tested on one spelling.
    ("log --output <path> (space-separated) into a protected repo", "refuse",
     lambda v, f, tmp: ["-C", str(f), "log", "-p", "--output",
                        str(v / ".git" / "config")]),
    ("grep -O<cmd>", "refuse",
     lambda v, f, tmp: ["-C", str(f), "grep", f"-O touch {tmp / 'PWN-GREP'}", "A"]),
]


@pytest.mark.parametrize("label,expect,build", READ_PATH_CASES,
                         ids=[c[0] for c in READ_PATH_CASES])
def test_a_read_verb_carrying_a_destination_or_command_is_checked(
        guarded, tmp_path, label, expect, build):
    """🔴 A VERB'S CLASSIFICATION SAYS NOTHING ABOUT ITS OPTIONS.

    `log` and `grep` are truthfully read verbs. MEASURED against an earlier
    revision, from an ALLOWED repo: `log -p --output=<victim>/.git/config`
    clobbered the victim's config and `grep -O<cmd>` ran the command — both rc
    0, both `exec`d on the fast path before any destination check. Same lesson
    as `status`, with a destination attached.
    """
    victim = _mkrepo(tmp_path / f"rp-v-{abs(hash(label)) % 9999}")
    fixture = _mkrepo(tmp_path / f"rp-f-{abs(hash(label)) % 9999}")
    (fixture / "A").write_text("hello\n")
    subprocess.run(["git", "-C", str(fixture), "add", "A"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(fixture), "commit", "-qm", "A"], check=True,
                   capture_output=True)
    gd = tmp_path / f"rp-g-{abs(hash(label)) % 9999}"
    norepo.install(gd, norepo.protected_paths(victim), [str(guarded.gitconfig)])
    env = dict(os.environ)
    env.pop("GIT_DIR", None)
    env["PATH"] = f"{gd}{os.pathsep}" + os.environ["PATH"]
    env["GIT_CONFIG_GLOBAL"] = str(guarded.gitconfig)
    before = (victim / ".git" / "config").read_text()
    p = subprocess.run(["git", *build(victim, fixture, tmp_path)], env=env,
                       capture_output=True, text=True, timeout=60)
    out = p.stdout + p.stderr
    if expect == "refuse":
        assert p.returncode == norepo.REFUSED_EXIT, f"{label} not refused:\n{out}"
        assert (victim / ".git" / "config").read_text() == before
        assert not (tmp_path / "PWN-GREP").exists(), "the -O command still ran"
    else:
        assert p.returncode == 0, (
            f"{label} was refused — an output destination OUTSIDE any protected "
            f"repo must be allowed:\n{out}")


def test_no_two_testlib_plugins_patch_Popen_the_same_way():
    """🔴 A sibling layer had `nolaunch` and its own plugin both subclass a
    PRISTINE `Popen` captured at import, so the second assignment ERASED the
    first and its controls still reported green — fail-open caused by a
    sibling rather than by absence.

    Ours does not collide: only `nolaunch_plugin` patches `Popen`, and it does
    so inside a hook rather than at import. Pinned so a future plugin that
    patches at import is caught here rather than by a silent disarm.
    """
    import subprocess as _sp
    pristine = _sp.Popen
    from testlib import nolaunch_plugin, norepo_plugin, spool_plugin  # noqa: F401
    assert _sp.Popen is pristine, (
        f"importing the testlib plugins patched Popen at import time: "
        f"{[c.__name__ for c in _sp.Popen.__mro__]}. Two such plugins silently "
        "erase each other.")
    patchers = [m for m, src in (
        ("nolaunch_plugin", (REPO_ROOT / "scripts/testlib/nolaunch_plugin.py").read_text()),
        ("spool_plugin", (REPO_ROOT / "scripts/testlib/spool_plugin.py").read_text()),
        ("norepo_plugin", (REPO_ROOT / "scripts/testlib/norepo_plugin.py").read_text()),
    ) if "Popen" in src]
    assert patchers == ["nolaunch_plugin"], (
        f"more than one testlib plugin now touches Popen: {patchers}. If two "
        "subclass the same pristine class, the second assignment wins and the "
        "first is inert while still reporting green.")


# --------------------------------------------------------------------------- #
# 4. THE RUNNER WIRING — tokens spelled on both sides of a process boundary
# --------------------------------------------------------------------------- #
def test_run_tests_scrubs_every_retargeting_variable():
    """🔴 The direct fix for the one mechanism the incident proved. A variable
    added to `norepo.RETARGETING_ENV` and not to the runner's `unset` is a
    variable the runner still inherits."""
    text = RUN_TESTS.read_text(encoding="utf-8")
    unset_block = re.search(r"^unset GIT_DIR[^\n]*(?:\n[ \t]+[A-Z_ \\]+)*", text,
                            re.MULTILINE)
    assert unset_block, "run-tests.sh no longer unsets the git retargeting variables"
    block = unset_block.group(0)
    missing = [v for v in norepo.RETARGETING_ENV if v not in block]
    assert not missing, (
        f"run-tests.sh does not unset {missing}. GIT_DIR alone is measured to "
        "override `git -C`; the rest are the same shape.")


def test_the_runner_and_the_shim_agree_on_every_shared_token():
    """🔴 Spelled on BOTH sides of a process boundary. A rename on one side alone
    leaves the runner's accounting matching nothing and printing a clean run."""
    text = RUN_TESTS.read_text(encoding="utf-8")
    for token in (norepo.REFUSED_PREFIX, norepo.CONTROL_PREFIX, norepo.PROBE_ENV,
                  norepo.GUARD_DIR_ENV, norepo.LOG_NAME):
        assert token in text, (
            f"run-tests.sh does not mention `{token}`; GUARD 9's accounting "
            "would match nothing and report every target clean.")


def test_the_runner_installs_the_shim_before_putting_it_on_PATH():
    """`install()` resolves the real binary through `shutil.which`, so a guard
    dir already on PATH would make the shim exec ITSELF. The ordering is the
    difference between a passthrough and an infinite fork loop."""
    text = RUN_TESTS.read_text(encoding="utf-8")
    assert text.index("python -m testlib.norepo") < text.index('export PATH="$GITGUARD_DIR')


def test_the_in_process_control_is_loaded_on_the_pytest_line():
    """🔴 `control` must be a claim about the TARGET, not about the runner.

    The runner-side probe proves the shim is armed in the RUNNER's environment.
    If that were the only control, every `refused=0` would describe the runner
    and the 32-row table would read as rigorous while measuring one thing
    thirty-two times. `testlib.norepo_plugin` fires the same probes from INSIDE
    the target's process, and the runner counts the two separately.

    MEASURED by breaking the plugin for EXACTLY ONE target: that target's row
    went `control=plugin:0 inherited:2`, the run FAILED naming it, and its
    sibling stayed `plugin:2`.
    """
    text = RUN_TESTS.read_text(encoding="utf-8")
    assert "-p testlib.norepo_plugin" in text, (
        "the in-process control is not loaded on the pytest line")
    assert (REPO_ROOT / "scripts" / "testlib" / "norepo_plugin.py").is_file()
    # And the runner must distinguish the two mechanisms, not sum them.
    assert "gctl_plug" in text and "gctl_inh" in text, (
        "the runner collapses the in-process and inherited controls into one "
        "number; a dead plugin on a pytest target would then look exactly like "
        "a healthy non-pytest target")
    assert "IN-PROCESS control" in text and "INHERITED control" in text


# ~20s: it drives two real runner copies. That is the price of a
# behavioural check, and a token check demonstrably could not see the
# mutant this replaces.
def test_a_dead_in_process_control_FAILS_the_run(tmp_path):
    """🔴 BEHAVIOURAL, because a token check cannot see a disabled branch.

    MEASURED: a mutant that turned the in-process requirement into `if [ 0 -eq 1
    ]` SURVIVED a structural test, because every token it looked for was still
    in the file. The guard's DESCRIPTION was intact and its body did nothing.

    So this drives a real runner COPY with one small target — the shape the
    repo's other runner regressions use — and requires that removing the plugin
    flag flips that target's row AND fails the run. Without this, `control=` is
    a number nobody has watched change.
    """
    src = RUN_TESTS.read_text(encoding="utf-8")

    def replace_array(text, name, body):
        i = text.index(f"{name}=(")
        j = text.index("\n)", i) + 2
        return text[:i] + f"{name}=(\n{body}\n)" + text[j:]

    target, floor = "scripts/collector/i3/tests", 12
    mini = replace_array(src, "HERMETIC_TARGETS", f"  {target}")
    mini = replace_array(mini, "TARGET_FLOORS", f'  "{target}|{floor}"')
    mini = replace_array(mini, "EXPECTED_SKIPS", "")
    broken = mini.replace("-p testlib.norepo_plugin --no-header", "--no-header")
    assert broken != mini, "the plugin flag anchor moved"

    env = dict(os.environ)
    # 🔴 The nesting flags MUST be cleared: this runner is being driven FROM a
    # target of another runner, and an inherited flag would make its own target
    # score as nested and report zero markers for the wrong reason.
    for k in ("DEVRC_TEST_GITGUARD_IN_SESSION", "DEVRC_TEST_SPOOL_IN_SESSION"):
        env.pop(k, None)

    out = {}
    for label, text in (("healthy", mini), ("broken", broken)):
        path = tmp_path / f"run-tests-{label}.sh"
        path.write_text(text)
        p = subprocess.run(["bash", str(path), "--set", "hermetic", str(REPO_ROOT)],
                           cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                           timeout=900)
        out[label] = (p.returncode, p.stdout + p.stderr)

    hrc, htxt = out["healthy"]
    brc, btxt = out["broken"]
    assert f"{target}  refused=0  control=plugin:2 inherited:2" in htxt, (
        f"healthy run did not report an in-process control:\n{htxt[-4000:]}")
    assert "control=plugin:0" in btxt and target in btxt, (
        f"breaking the plugin did not flip the row:\n{btxt[-4000:]}")
    assert brc != 0, "a dead in-process control did NOT fail the run"
    assert "IN-PROCESS control" in btxt, (
        "the run failed but not for this reason — a different failure is being "
        f"read as the guard working:\n{btxt[-4000:]}")


def test_the_control_tab_is_a_LITERAL_tab_not_a_BRE_escape():
    r"""🔴 VALIDATE THE INSTRUMENT BEFORE READING ITS VERDICT.

    In a BRE, `grep -c '^git(control)\tvia=plugin'` treats `\t` as a literal
    `t`, warns "stray \ before t", matches NOTHING, and reports control=0 for
    EVERY target — the accounting failing while the guard is perfectly healthy,
    which is indistinguishable from the guard being dead. MEASURED on the first
    revision of this accounting.

    🔴 AND THIS TEST ONLY READS CODE. Its first revision searched the whole file
    and matched the COMMENT that documents the bug, so it failed on a correct
    tree — a guard walkable by prose, in the test written to stop exactly that.
    """
    lines = [ln for ln in RUN_TESTS.read_text(encoding="utf-8").splitlines()
             if not ln.lstrip().startswith("#")]
    code = "\n".join(lines)
    assert "GITGUARD_TAB=" in code, "no literal-tab variable"
    bad = [ln for ln in lines if "git(control)" in ln and "\\t" in ln]
    assert not bad, (
        "the control counter matches a BRE `\\t`, which is a literal `t` and "
        f"matches nothing:\n" + "\n".join(bad))


def test_the_nested_session_flag_keeps_child_pytests_out_of_the_ledger():
    """Some tests START another pytest. A nested session must still be PROTECTED
    but must not write into the runner's per-target ledger, where an extra
    control reads as a miscount. Same shape as GUARD 8's NESTED_ENV."""
    from testlib import norepo_plugin
    assert norepo_plugin.NESTED_ENV
    text = RUN_TESTS.read_text(encoding="utf-8")
    assert f"unset {norepo_plugin.NESTED_ENV}" in text, (
        "a fresh RUN must clear the nesting flag, or a runner driven FROM a "
        "test inherits it and every target is scored as nested")


def test_every_target_kind_is_accounted_by_guard_9():
    """The non-pytest targets are the half no conftest can ever reach, so the
    runner must account HOOK_TESTS and SHELL_TESTS exactly like a pytest one."""
    text = RUN_TESTS.read_text(encoding="utf-8")
    assert text.count("_gitguard_account") >= 3, (
        "GUARD 9 accounts fewer than the three target kinds (pytest, HOOK_TESTS, "
        "SHELL_TESTS); an unaccounted kind reads as a clean zero.")
    assert text.count("_gitguard_probe") >= 3, (
        "a target with no probe has no positive control, so its refused=0 is "
        "indistinguishable from a guard wired to nothing.")


# --------------------------------------------------------------------------- #
# 5. THE REFUTATIONS — pinned so they are not re-derived
# --------------------------------------------------------------------------- #
def test_the_drift_check_ssh_stub_never_executes_its_payload():
    """🔴 REFUTED THEORY #1: "drift-check's remote leg runs the payload LOCALLY
    under the stub ssh, with DRIFT_REPO unset, so it operates on the real clone."

    The stub drains stdin and exits. It never execs anything. Whatever wrote to
    the operator's clone, it was not this.
    """
    src = DRIFT_TESTS.read_text(encoding="utf-8")
    body = src[src.index("def stub_ssh"):]
    body = body[:body.index("\n    def ", 1)]
    assert 'body = ["cat >/dev/null"]' in body, (
        "the drift-check ssh stub no longer starts by DRAINING stdin. If it now "
        "EXECUTES the payload, the remote leg runs locally against "
        "$HOME/workspace/devrc and refuted theory #1 becomes true.")
    # The stub's whole body is: drain, optionally echo a canned line, exit N.
    # Anything that could RUN what it drained — a shell, an eval, `"$@"` — would
    # make the remote leg execute locally, which is theory #1.
    assert body.count("body.append(") == 2, (
        "the ssh stub gained a body line; check it does not RUN what it drained.")
    for runner in ('"$@"', "eval", "bash", "sh -s", "source ", "exec "):
        assert runner not in body, (
            f"the drift-check ssh stub now contains `{runner}` — if it EXECUTES "
            "the piped payload, drift-check's remote leg runs LOCALLY against "
            "$HOME/workspace/devrc and refuted theory #1 becomes true.")


def test_a_pre_push_hook_does_not_inherit_git_dir(tmp_path):
    """🔴 REFUTED THEORY #2: "githooks/tests-on-push.sh runs the suite from a
    pre-push hook, where git exports GIT_DIR."

    Measured here rather than asserted from memory, because the whole point of
    theory #1 and #2 was that both were plausible and both were wrong. If a
    future git DOES start exporting it, this test goes red and the theory is
    live again — which is the useful direction for it to fail in.
    """
    bare = _mkrepo(tmp_path / "o.git", bare=True)
    clone = _mkrepo(tmp_path / "c")
    subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", str(bare)],
                   check=True, capture_output=True)
    seen = tmp_path / "seen.txt"
    write_exec(clone / ".git" / "hooks" / "pre-push",
               f'cat >/dev/null\nenv | grep "^GIT_" | sort > "{seen}"\nexit 1\n')
    subprocess.run(["git", "-C", str(clone), "push", "origin", "main"],
                   capture_output=True, text=True)
    names = {ln.split("=", 1)[0] for ln in seen.read_text().splitlines()}
    assert "GIT_DIR" not in names, (
        f"git now exports GIT_DIR to pre-push hooks ({sorted(names)}). "
        "githooks/tests-on-push.sh runs the WHOLE suite from that hook, so "
        "every fixture's `git -C <tmp_path>` would retarget the real clone.")


def test_an_ambient_git_dir_reproduces_the_incident_signature(tmp_path):
    """🔴 THE MECHANISM, MEASURED — and the reason a call-site audit finds nothing.

    Replays `scripts/repo-cos/tests/test_prescan.py::_init_clone` verbatim: every
    call correctly `-C`-bound to a tmp_path fixture. One ambient GIT_DIR names
    the victim, which no command mentions. The result is the incident's own
    signature: the victim's branch renamed, its HEAD repointed, its committer
    identity rewritten to the fixture's, the fixture commit landed in it, and
    that commit PUBLISHED to the victim's own remote.

    This test does NOT run under the guard — it is the pre-fix behaviour, kept
    red-in-spirit so the fix above is never mistaken for a fix to a theory.
    """
    origin = _mkrepo(tmp_path / "sacrificial-origin.git", bare=True)
    victim = _mkrepo(tmp_path / "victim")
    subprocess.run(["git", "-C", str(victim), "config", "user.email", "real@op"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(victim), "remote", "add", "origin", str(origin)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(victim), "push", "-q", "origin", "main"],
                   check=True, capture_output=True)

    env = dict(os.environ)
    env["GIT_DIR"] = str(victim / ".git")
    env["PATH"] = os.environ["PATH"]
    # 🔴 The guard is NOT on PATH for this one: it is the pre-fix control.
    gd = env.get(norepo.GUARD_DIR_ENV)
    if gd:
        env["PATH"] = os.pathsep.join(
            p for p in env["PATH"].split(os.pathsep) if p != gd)

    bare = tmp_path / "fixture" / "remote.git"
    clone = tmp_path / "fixture" / "clone"
    bare.parent.mkdir(parents=True, exist_ok=True)
    for argv in (["git", "init", "--quiet", "--bare", str(bare)],
                 ["git", "clone", "--quiet", str(bare), str(clone)],
                 ["git", "-C", str(clone), "config", "user.email", "t@t"],
                 ["git", "-C", str(clone), "config", "user.name", "t"]):
        subprocess.run(argv, env=env, capture_output=True, text=True)
    clone.mkdir(parents=True, exist_ok=True)
    (clone / "seed.py").write_text("x = 1  # seed\n")
    for argv in (["git", "-C", str(clone), "add", "seed.py"],
                 ["git", "-C", str(clone), "commit", "--quiet", "-m", "seed"],
                 ["git", "-C", str(clone), "push", "--quiet", "origin", "HEAD:trunk"],
                 ["git", "-C", str(clone), "branch", "-M", "trunk"]):
        subprocess.run(argv, env=env, capture_output=True, text=True)

    state = _victim_state(victim)
    assert state["branches"] == ["refs/heads/trunk"], (
        f"GIT_DIR no longer retargets `git -C` (branches={state['branches']}). "
        "If that is genuinely true of this git, say so — but do not delete the "
        "guard: it is the only thing that made the class impossible.")
    assert state["HEAD"] == "refs/heads/trunk"
    assert _git("-C", str(victim), "config", "--get", "user.email").stdout.strip() == "t@t"
    assert "seed.py" in _git("-C", str(victim), "ls-tree", "--name-only", "HEAD").stdout
    pushed = _git("-C", str(origin), "for-each-ref", "--format=%(refname)").stdout
    assert "refs/heads/trunk" in pushed, (
        "the fixture commit did not reach the victim's own remote — the half of "
        "the incident that reached the PUBLIC repo")
