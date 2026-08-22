"""🔴 The guard: no test may WRITE to a git repo it does not own.

This file exists because the suite drove `git` against the operator's REAL
clone and destroyed `main` on the remote. The ordering is the diagnosis:

    $ git -C ~/workspace/devrc reflog show trunk
    5d91acdd trunk@{0}: Branch: renamed refs/heads/main to refs/heads/trunk
    5d91acdd trunk@{1}: commit: seed
    2e7a85b3 trunk@{2}: commit: c

Local corruption at **19:21:35Z**; the remote push storm — ~40 pushes onto
`refs/heads/main` in 43 seconds — began **19:28:14Z**, seven minutes LATER. The
local writes came first and the pushes followed them, which rules out an
independently-poisoned remote and is why a push-scoped or remote-scoped guard
would have been useless.

🔴 HOW THIS FILE IS BUILT, AND WHY THE PREVIOUS SHAPE WAS WORTH ~4× ITS VALUE
------------------------------------------------------------------------------
The previous revision asserted, for each vector, "the guard returned 99 AND the
victim is byte-identical". Running each vector's identical command **with the
guard removed** showed that of 11 entries only **3** could move the victim on
an axis the comparison actually looked at: 4 wrote bytes the comparison was
blind to, and 4 moved nothing at all — for those, "the victim is unchanged" was
equally true with the guard DELETED. A vector that cannot move the victim is
not evidence about the guard.

So every negative control here runs THREE ways, and `_expect_closed` is the
only way a hazard is scored:

  NOOP      build the fixtures and run NOTHING. The victim must not move.
            This is the probe validating ITSELF — a sibling implementation had
            two findings contaminated by the probe creating the very file it
            then scored as a leak.
  UNGUARDED run the identical argv against the real binary with the shim off
            PATH. The victim MUST move, or the case is vacuous and is reported
            as such rather than counted.
  GUARDED   run it through the shim. The victim must not move, and the refusal
            must carry this policy's banner.

`_victim_state` is a full byte manifest of the victim tree plus its ref list,
because the previous narrow tuple (HEAD, branch, count, user.email, one file)
was blind to loose objects, to `.git/index`, and to `.gitconfig` creation —
which is what made four live vectors score as uninformative.

SCOPE, stated so the title cannot be read as more than it is:
  * This covers what `testlib/nogit_plugin.py` is loaded into, which is
    `scripts/tests` and no other target. See that module's REGISTRATION SCOPE
    header for the measurement and why the gap is not closed here.
  * It bounds writes by TARGET PATH, so a test that clobbers PATH wholesale is
    outside it.
  * It cannot bound arbitrary SHELL run by an alias or hook — only the `git`
    such code spawns. `test_a_shell_alias_writing_the_victim_is_NOT_covered`
    pins that limit as a measured fact rather than leaving it implied.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from testlib import nogit  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402
from testlib import nogit_plugin  # noqa: E402

TIMEOUT = 90


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
@contextlib.contextmanager
def _l2_off():
    """🔴 TAKE THE IN-PROCESS LAYER OUT OF THE WAY FOR AN UNGUARDED CONTROL.

    MEASURED, and it silently invalidated thirteen cases on the first run of
    this rebuilt battery: every "unguarded" control launched from inside the
    pytest process goes through `subprocess.Popen`, which L2 has patched — so
    `subprocess.run([<real git>, …])` was REWRITTEN to the shim and came back
    **rc 99 with the banner**. The harness reported "this vector is VACUOUS"
    for thirteen vectors that are demonstrably live outside pytest.

    Removing the shim from `PATH` is NOT enough, because L2 keys on the
    basename of an ABSOLUTE argv[0] — which is exactly its job. So an
    unguarded control has to unwind L1 *and* L2, and a control that unwinds
    only one is measuring the guard it forgot about.

    This walks the `Popen` MRO down to the real `subprocess.Popen` and puts it
    back for the duration, then restores the full chain — including the
    launcher policy's patch, which sits in the same chain.
    """
    saved = subprocess.Popen
    base = next((c for c in saved.__mro__
                 if c.__module__ == "subprocess" and c.__name__ == "Popen"),
                saved)
    subprocess.Popen = base
    try:
        yield
    finally:
        subprocess.Popen = saved


def _install(stub: Path, **kw):
    """`nogit.install`, but resolving the REAL git and dodging L2.

    🔴 A test that calls `nogit.install()` naively gets a shim whose baked
    `real` is THE SESSION'S OWN SHIM — `shutil.which("git")` inside a live
    session resolves to PATH[0], which is the session stub dir. The result is a
    double shim: the new policy runs, then execs the old one, which applies the
    SESSION's roots on top. Every assertion about the new shim's roots then
    measures the wrong policy.

    `exec_path_farm()` has the same problem one level down — it asks the
    resolved binary for `--exec-path` through `subprocess`, which L2 rewrites.
    """
    saved = os.environ.get("PATH")
    os.environ["PATH"] = _real_path()
    try:
        with _l2_off():
            return nogit.install(stub, **kw)
    finally:
        if saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved


def _handshake(stub: Path):
    """`nogit.handshake`, with L2 out of the way.

    In production `_inherited_stub_dir()` runs BEFORE `_patch_subprocess`, so
    the real call is never redirected. Inside a live session it would be — and
    a foreign `git` would then be answered by the SESSION's shim, which is how
    the foreign-shim rejection tests first passed for the wrong reason.
    """
    with _l2_off():
        return nogit.handshake(stub)


def _real_path() -> str:
    """PATH with the shim directory removed — the ambient, unprotected one."""
    stub = os.environ.get(nogit.STUB_DIR_ENV, "")
    farm = str(nogit.exec_path_dir(stub)) if stub else ""
    return os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and p != stub and p != farm)


def _real() -> str:
    real = shutil.which("git", path=_real_path())
    assert real is not None, "no real git outside the shim dir"
    return real


def _git(*args: str, cwd: Path, env: dict | None = None,
         stdin_text: str | None = None) -> subprocess.CompletedProcess:
    """Run git with an EXPLICIT cwd — never inheriting this process's.

    `cwd` is keyword-only and required on purpose: a helper with a default cwd
    is how a test silently targets whatever directory the runner happened to
    start in, which is the shape that caused the incident.
    """
    return subprocess.run(["git", *args], cwd=str(cwd), env=env,
                          input=stdin_text, capture_output=True, text=True,
                          timeout=TIMEOUT)


def _make_repo(path: Path, origin: str | None = None,
               tree: dict[str, str] | None = None) -> Path:
    """A real, minimal git repo at `path`, built with the REAL binary.

    It bypasses the shim deliberately: these repos are the fixtures the tests
    then point the shim at, and building them through the shim would make every
    test depend on the very thing under test.

    🔴 `tree` EXISTS BECAUSE AN EMPTY SEED MADE A VECTOR VACUOUS. The previous
    version always seeded with a single `--allow-empty` commit, so the fixture's
    HEAD tree was EMPTY and `GIT_WORK_TREE=<victim> git -C <fixture> checkout
    -f HEAD` wrote nothing anywhere — the negative control passed against a
    command that could not have done damage in the first place. Give a fixture
    a tree that DIFFERS from the victim's and the same command clobbers the
    victim's working files.
    """
    real = _real()
    path.mkdir(parents=True, exist_ok=True)
    def run(*a):
        return subprocess.run([real, *a], cwd=str(path), check=True,
                              capture_output=True, timeout=TIMEOUT)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.invalid")
    # 🔴 A PER-PATH IDENTITY, SO TWO FIXTURES CANNOT SHARE A COMMIT SHA.
    # MEASURED as an intermittent failure: with every repo seeded identically
    # (`t <t@example.invalid>`, message "seed", empty tree), the victim's and
    # the fixture's seed commits had the SAME sha whenever both were created in
    # the same second. `GIT_OBJECT_DIRECTORY=<victim>/.git/objects git -C
    # <fixture> commit` then "worked" — because the fixture's HEAD object
    # happened to exist in the victim's object store — and failed `fatal: could
    # not parse HEAD` when the clock ticked between them. A vector whose
    # liveness depends on a sha collision is not a vector.
    run("config", "user.name", f"t-{path.name}")
    if tree:
        for rel, content in tree.items():
            p = path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", "seed-tree")
    else:
        run("commit", "-q", "--allow-empty", "-m", "seed")
    if origin is not None:
        run("remote", "add", "origin", origin)
    return path


def _victim(path: Path) -> Path:
    """A repo that must survive every negative control untouched."""
    real = _real()
    _make_repo(path)
    (path / "keep.txt").write_text("PRECIOUS", encoding="utf-8")
    for a in (("add", "keep.txt"), ("commit", "-q", "-m", "victimseed")):
        subprocess.run([real, "-C", str(path), *a], check=True,
                       capture_output=True, timeout=TIMEOUT)
    return path


def _manifest(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        k = str(p.relative_to(root))
        if p.is_file():
            try:
                out[k] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
            except OSError:
                out[k] = "<unreadable>"
        elif p.is_dir():
            out[k + "/"] = "<dir>"
        else:
            out[k] = "<other>"
    return out


def _victim_state(repo: Path) -> tuple:
    """Everything an intruder could disturb, as one comparable value.

    🔴 A FULL BYTE MANIFEST, not a hand-picked tuple. The previous version
    compared (HEAD, branch, commit count, user.email, keep.txt) and was
    therefore blind to loose objects (`GIT_OBJECT_DIRECTORY`,
    `GIT_COMMON_DIR`, `hash-object -w`), to `.git/index` (`GIT_INDEX_FILE`,
    `status`), and to a `.gitconfig` created beside the repo
    (`GIT_CONFIG_GLOBAL`/`_SYSTEM`). Four live vectors scored as "the victim
    did not change" when bytes had landed in it.

    The ref list is kept alongside because a push creates a ref whose only
    on-disk trace may be inside `packed-refs`, and reading it through git is
    the honest way to see it.
    """
    real = _real()
    refs = subprocess.run(
        [real, "-C", str(repo), "for-each-ref",
         "--format=%(refname) %(objectname)"],
        capture_output=True, text=True, timeout=TIMEOUT).stdout
    head = subprocess.run(
        [real, "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=TIMEOUT).stdout.strip()
    return (head, refs, _manifest(repo))


def _env(*, allowed: str, denied: str, path: str | None = None) -> dict:
    e = dict(os.environ)
    e[nogit.ROOTS_ENV] = allowed
    e[nogit.DENY_ROOTS_ENV] = denied
    if path is not None:
        e["PATH"] = path
        # Removing the shim from PATH is not enough to unguard a call: the
        # exec-path farm would still hand `git` back to the shim for any child.
        e.pop("GIT_EXEC_PATH", None)
    return e


# --------------------------------------------------------------------------- #
# 🔴 THE THREE-WAY HAZARD HARNESS
# --------------------------------------------------------------------------- #
def _stage(root: Path, builder) -> tuple[Path, list[str], Path, dict, Path]:
    """Build one throwaway world and ask `builder` for the command to run.

    `builder(root, victim, allowed) -> (argv_after_git, cwd, extra_env, home)`
    """
    victim = _victim(root / "victim")
    allowed = root / "allowed"
    allowed.mkdir(parents=True, exist_ok=True)
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    argv, cwd, extra, home_override = builder(root, victim, allowed)
    return victim, argv, cwd, extra, (home_override or home)


def _expect_closed(tmp_path: Path, builder, *, name: str) -> None:
    """Score one hazard the only way a hazard may be scored.

    Fails with a DIFFERENT message for each of the three ways this can go
    wrong, because "the victim did not change" means three different things
    depending on which run produced it.
    """
    # ---- NOOP: does the probe itself disturb the victim? -------------------
    noop_root = tmp_path / "noop"
    noop_root.mkdir()
    victim, _argv, _cwd, _extra, _home = _stage(noop_root, builder)
    assert _victim_state(victim) == _victim_state(victim), (
        f"{name}: the victim's state is not stable between two reads with "
        "NOTHING run — this probe cannot score anything.")
    before_noop = _victim_state(victim)
    after_noop = _victim_state(victim)
    assert before_noop == after_noop

    # ---- UNGUARDED: is the vector LIVE? ------------------------------------
    un_root = tmp_path / "unguarded"
    un_root.mkdir()
    victim, argv, cwd, extra, home = _stage(un_root, builder)
    env = _env(allowed=str(un_root / "allowed"), denied=str(victim),
               path=_real_path())
    env["HOME"] = str(home)
    env.update(extra)
    before = _victim_state(victim)
    with _l2_off():
        unguarded = subprocess.run([_real(), *argv], cwd=str(cwd), env=env,
                                   capture_output=True, text=True,
                                   timeout=TIMEOUT)
    after = _victim_state(victim)
    assert unguarded.returncode != nogit.BLOCK_EXIT, (
        f"{name}: the UNGUARDED control was itself refused by this policy "
        f"(rc={nogit.BLOCK_EXIT}). The control is not unguarded — see "
        "`_l2_off`, which exists because L2 rewrites an absolute-path git "
        "even when the shim is off PATH.")
    assert after != before, (
        f"{name}: with the guard REMOVED the victim did not change "
        f"(rc={unguarded.returncode}, stderr={unguarded.stderr.strip()[:200]!r}). "
        "This vector is VACUOUS: the 'refused' result below would be equally "
        "true of a guard that had been deleted, so it is not evidence. Either "
        "make the command able to reach the victim, or drop the case.")

    # ---- GUARDED -----------------------------------------------------------
    g_root = tmp_path / "guarded"
    g_root.mkdir()
    victim, argv, cwd, extra, home = _stage(g_root, builder)
    env = _env(allowed=str(g_root / "allowed"), denied=str(victim))
    env["HOME"] = str(home)
    env.update(extra)
    before = _victim_state(victim)
    guarded = _git(*argv, cwd=cwd, env=env)
    after = _victim_state(victim)
    assert after == before, (
        f"{name}: the guard returned {guarded.returncode} but the victim "
        f"CHANGED.\n  argv: git {' '.join(argv)}\n"
        f"  stderr: {guarded.stderr.strip()[:300]!r}")


def _expect_refused(tmp_path: Path, builder, *, name: str) -> None:
    """`_expect_closed`, plus: the refusal must be THIS policy's.

    Split from `_expect_closed` because a few hazards are closed by FORWARDING
    the call in a harmless form rather than by refusing it — `status` is
    forwarded with `--no-optional-locks` and legitimately exits 0. Requiring a
    banner there would be asserting the wrong thing.
    """
    _expect_closed(tmp_path, builder, name=name)
    root = tmp_path / "refused"
    root.mkdir()
    victim, argv, cwd, extra, home = _stage(root, builder)
    env = _env(allowed=str(root / "allowed"), denied=str(victim))
    env["HOME"] = str(home)
    env.update(extra)
    got = _git(*argv, cwd=cwd, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        f"{name}: not refused (rc={got.returncode}). "
        f"stdout={got.stdout[:200]!r} stderr={got.stderr[:300]!r}")
    assert nogit.BANNER in got.stderr, (
        f"{name}: refused, but without this policy's banner — a firing must be "
        f"identifiable as this guard rather than as a git error: "
        f"{got.stderr!r}")


# --------------------------------------------------------------------------- #
# RESOLUTION + ORDERING
# --------------------------------------------------------------------------- #
def test_git_resolves_into_the_shim_dir(no_real_git_writes):
    resolved = shutil.which("git")
    assert resolved is not None, "no git on PATH at all — the shim is missing"
    assert Path(resolved).parent == Path(no_real_git_writes), (
        f"`git` resolves to {resolved}, not into the shim dir "
        f"{no_real_git_writes} — every write in this session reaches the real "
        "binary unchecked")


def test_no_path_entry_before_the_shim_provides_a_git(no_real_git_writes):
    """The shim must precede every directory that could answer to `git`.

    🔴 NOT "the shim dir is PATH[0]" — measured, it is not, and asserting that
    was wrong rather than merely strict. The launcher policy's stub dir is
    prepended by its own session fixture, so whichever of the two autouse
    fixtures runs last takes position 0.

    🔴 AND THIS MEASURES THIS PROCESS'S PATH, WHICH IS NOT WHERE THE ESCAPE
    WAS. `test_git_spawned_children_resolve_git_to_the_shim` is the one that
    covers a child of git itself; this one cannot see that at all.
    """
    entries = [p for p in os.environ["PATH"].split(os.pathsep) if p]
    shim = str(no_real_git_writes)
    assert shim in entries, "the shim dir is not on PATH at all"
    before = entries[:entries.index(shim)]
    shadowing = [d for d in before if os.path.exists(os.path.join(d, "git"))]
    assert not shadowing, (
        f"these PATH entries precede the shim and provide their own `git`: "
        f"{shadowing}. Every git call in this session would reach them "
        "instead, and the policy would be silently inert.")


def test_the_exec_path_farm_is_not_itself_on_path(no_real_git_writes):
    """The farm holds ~184 `git-*` symlinks; PATH is not where they belong.

    It is reached only through `GIT_EXEC_PATH`. On PATH it would shadow any
    same-named tool for every process in the session.
    """
    farm = str(nogit.exec_path_dir(no_real_git_writes))
    entries = [p for p in os.environ["PATH"].split(os.pathsep) if p]
    assert farm not in entries, (
        f"the exec-path farm {farm} is on PATH; it must be reachable only "
        "through GIT_EXEC_PATH")
    assert os.environ.get("GIT_EXEC_PATH") == farm, (
        "GIT_EXEC_PATH does not point at the farm, so every child git spawns "
        "resolves `git` to the real binary — see nogit.exec_path_farm()")


def test_git_is_not_also_claimed_by_the_launcher_policy():
    """🔴 The seam between the two policies, pinned.

    `nolaunch`'s stubs are RECORD-ONLY: they log the argv and exit 0 with no
    output. If `git` were ever added to `HOST_LAUNCHERS`, that stub would
    shadow this one and every git call in the suite would return success with
    empty stdout: `rev-parse` answering nothing, `ls-files` listing nothing,
    and every content gate scanning zero files while passing.
    """
    from testlib import nolaunch  # noqa: PLC0415 — local, to keep the seam explicit
    assert "git" not in nolaunch.HOST_LAUNCHERS, (
        "`git` is in nolaunch.HOST_LAUNCHERS, so its RECORD-ONLY stub would "
        "shadow this policy's shim and answer every git call with exit 0 and "
        "empty output — fabricating the answers this module exists to refuse "
        "to fabricate.")


def test_the_session_denies_the_tree_under_test(no_real_git_writes):
    """The deny root must be the repo, or the policy is inert where it matters."""
    denied = os.environ[nogit.DENY_ROOTS_ENV].split(":")
    assert str(nogit.repo_root()) in denied, (
        f"the tree under test ({nogit.repo_root()}) is not in "
        f"{nogit.DENY_ROOTS_ENV}={denied!r}. In the nix sandbox the source is "
        "unpacked under $TMPDIR, so WITHOUT this entry every write to the tree "
        "under test would sit inside an allowed root and be permitted.")


# --------------------------------------------------------------------------- #
# THE LEDGERS
# --------------------------------------------------------------------------- #
def test_the_read_verb_ledger_is_pinned():
    """Fails when the read set GROWS or SHRINKS.

    Growing it is how a write gets reclassified as a read — the failure mode
    that put `remote set-url` through in the first place, and that kept
    `hash-object` (which writes with `-w`) on this list. Shrinking it is how
    the policy goes permanently red.
    """
    assert set(nogit.GIT_READ_VERBS) == {
        "status", "log", "show", "diff", "cat-file", "ls-files", "ls-tree",
        "ls-remote", "rev-parse", "rev-list", "describe", "blame", "grep",
        "shortlog", "show-ref", "for-each-ref", "merge-base",
        "name-rev", "check-ignore", "check-attr", "count-objects",
        "verify-pack", "var", "help", "version", "annotate", "whatchanged",
        "cherry", "diff-tree", "diff-index", "diff-files",
        "patch-id", "get-tar-commit-id", "check-mailmap", "column",
    }
    # 🔴 The verbs REMOVED from this ledger, pinned as absent by name. Each was
    # measured to write, and each was invisible to the predecessor of
    # `test_no_verb_on_the_read_ledger_can_change_a_repo` because that test
    # parametrised twelve verbs that were never on the ledger at all.
    for writes in ("hash-object", "symbolic-ref", "interpret-trailers", "fsck"):
        assert writes not in nogit.GIT_READ_VERBS, (
            f"`{writes}` has a WRITING form and is back on the read ledger")
        assert writes in nogit.GIT_DUAL_VERBS, (
            f"`{writes}` is neither a ledgered read nor a dual verb, so its "
            "reading form is refused outright — a permanently-red gate")


@pytest.mark.parametrize("verb", sorted(nogit.GIT_READ_VERBS))
def test_no_verb_on_the_read_ledger_can_change_a_repo(
        verb, tmp_path, no_real_git_writes):
    """🔴 EXERCISES EVERY LEDGERED VERB. The previous version of this test
    parametrised twelve mutating verbs — `commit`, `push`, `checkout`, … —
    and asserted each was ABSENT from the ledger. Not one of them was ever on
    it, so the test was vacuous by construction: it could not fail, and it did
    not notice `hash-object` or `status`, which WERE on the ledger and DO
    write.

    This runs each ledgered verb against a repo the session denies, in its
    plainest form, and asserts the repo is byte-identical afterwards. A verb
    that cannot run in that form is skipped BY NAME rather than silently
    passing.
    """
    victim = _victim(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    before = _victim_state(victim)
    got = _git(verb, cwd=victim, env=env)
    # Some verbs need an argument; a usage error is fine, a WRITE is not.
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"`git {verb}` is on the READ ledger but the policy refused it — the "
        f"ledger and the shim disagree: {got.stderr[:200]!r}")
    after = _victim_state(victim)
    assert after == before, (
        f"`git {verb}` is on the READ ledger but CHANGED a repo this session "
        f"denies. Ledger rule: a verb belongs there only if it cannot modify "
        f"the repository, its config, its refs, its index or its worktree.\n"
        f"  changed: "
        f"{sorted(k for k in set(after[2]) | set(before[2]) if after[2].get(k) != before[2].get(k))[:6]}")


def test_the_shim_has_no_env_readable_mode(tmp_path):
    """🔴 `DEVRC_TEST_GIT_MODE=audit` used to disarm the whole policy.

    MEASURED against the previous revision: `DEVRC_TEST_GIT_MODE=audit git -C
    <victim> commit --allow-empty -m AUDITPWN` returned 0 and the victim
    received the commit. One inherited variable, guard gone.

    The test that was supposed to prevent it asserted only that the STRING
    `${DEVRC_TEST_GIT_MODE:-block}` appeared in the generated body — which is
    exactly what a shim running in audit mode also contains. It could not see
    an audit-mode run at all.

    This asserts the generated body never reads the variable, which is a
    structural claim; `test_an_audit_mode_env_var_cannot_disarm_the_guard` is
    the live one.
    """
    body = nogit.git_body("/bin/true", Path("/dev/null"), stub_dir=tmp_path)
    assert nogit.MODE_ENV not in body, (
        f"the shim still reads {nogit.MODE_ENV} from the environment. Mode is "
        "a bake-time argument to install() precisely so that nothing an "
        "inherited environment carries can turn the guard off.")


def test_the_env_ledger_no_longer_holds_the_known_non_vectors():
    """The three names removed for not receiving bytes, pinned as absent.

    This is the STRUCTURAL half and it is deliberately weak — see
    `test_every_env_ledger_entry_is_a_live_write_vector` for the half that
    could actually have caught the mistake. The previous version of this file
    had only the structural half, and it PINNED
    `GIT_ALTERNATE_OBJECT_DIRECTORIES` AS CORRECT.
    """
    for bad in ("GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        assert bad not in nogit.GIT_LOCATION_ENV, (
            f"{bad} does not receive bytes — treating it as a write target "
            "refuses the safe case and inflates the count of vectors this "
            "policy can claim to defend")


@pytest.mark.parametrize("var", sorted(nogit.GIT_LOCATION_ENV))
def test_every_env_ledger_entry_is_a_live_write_vector(
        var, tmp_path, no_real_git_writes):
    """🔴 THE TEST THAT WOULD HAVE CAUGHT THE MISTAKE, AND ITS PREDECESSOR
    COULD NOT.

    The previous file asserted `set(GIT_LOCATION_ENV) == {…literal set…}`. That
    is a membership claim, so it PASSED for
    `GIT_ALTERNATE_OBJECT_DIRECTORIES` — a READ-ONLY additional object store
    that cannot redirect a write anywhere. Measured, with it armed at the
    victim and the guard removed:

        GIT_ALTERNATE_OBJECT_DIRECTORIES=<victim>/.git/objects \\
          git -C <fixture> commit --allow-empty -m PWNED
        -> rc 0; the object landed in <fixture>/.git/objects/c4/…
           and a full byte manifest of the victim was UNCHANGED.

    It had been "caught" by a negative control that armed it at a path-shaped
    value, which measures whether the guard READS the variable, not whether the
    variable is a write target.

    This EXERCISES each entry: arm it at the victim with the guard removed and
    require the victim to move. A ledger entry that cannot move the victim
    fails here and must be removed, not documented.
    """
    victim, args, tree = _ENV_VECTORS[var]
    root = tmp_path / "live"
    root.mkdir()
    v = _victim(root / "victim")
    fixture = _make_repo(root / "allowed" / "fix", tree=tree)
    env = _env(allowed=str(root / "allowed"), denied=str(v),
               path=_real_path())
    env["HOME"] = str(root / "home")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    env[var] = str(v / victim) if victim else str(v)

    before = _victim_state(v)
    with _l2_off():
        got = subprocess.run([_real(), *args], cwd=str(fixture), env=env,
                             capture_output=True, text=True, timeout=TIMEOUT)
    after = _victim_state(v)
    assert after != before, (
        f"{var} is on GIT_LOCATION_ENV but, armed at the victim WITH THE "
        f"GUARD REMOVED, it moved nothing (rc={got.returncode}, "
        f"stderr={got.stderr.strip()[:200]!r}). It is not a write vector, so "
        "refusing it is a pure false positive and counting it as defended "
        "inflates this policy's coverage. Remove it from the ledger.")


# 🔴 One LIVE command per ledger entry. Every one of these was chosen by
# running it with the guard removed and watching the victim move; the
# commented-out shapes are the ones that did NOT.
#
#   var -> (path under the victim, argv, fixture tree)
#
# `GIT_WORK_TREE` uses `checkout -f HEAD` with a fixture whose tree DIFFERS
# from the victim's, which clobbers `keep.txt`. The previous file paired it
# with `add -A` as well: measured, `add -A` under `GIT_WORK_TREE` writes the
# objects and the index into the FIXTURE and leaves the victim completely
# untouched, so that entry was vacuous.
_ENV_VECTORS = {
    "GIT_DIR": (".git", ("commit", "--allow-empty", "-m", "PWNED"), None),
    # 🔴 THREE SHAPES WERE TRIED AND ONLY THIS ONE IS DETERMINISTIC.
    # Measured with the object store redirected to the victim:
    #   branch <name>          -> fatal: not a valid branch point
    #   commit --allow-empty   -> fatal: could not parse HEAD
    #   tag / update-ref / gc  -> nonexistent object / invalid ref
    # `commit` LOOKED live for a while, and only because the victim and the
    # fixture shared a seed commit sha. `config --local` writes
    # `<victim>/.git/config` and needs no object at all — and it is the exact
    # `config core.bare true` damage the incident's reflog recorded, arriving
    # through a redirection instead of through an argument.
    "GIT_COMMON_DIR": (".git", ("config", "--local", "nogit.pwned", "1"),
                       None),
    "GIT_WORK_TREE": ("", ("checkout", "-f", "HEAD"),
                      {"a.txt": "FIXTURE\n", "keep.txt": "CLOBBERED\n"}),
    # 🔴 NOT `commit`. With the object dir redirected, `commit` must read the
    # fixture's own HEAD commit out of the VICTIM's object store and dies
    # `fatal: could not parse HEAD` — it only ever appeared live because the
    # two fixtures shared a seed sha (see `_make_repo`). `hash-object -w`
    # reads no HEAD and writes the blob straight into the redirected store:
    # measured, the victim's loose-object count went 2 -> 3.
    "GIT_OBJECT_DIRECTORY": (".git/objects",
                             ("hash-object", "-w", "a.txt"),
                             {"a.txt": "OBJECT-VECTOR\n"}),
    "GIT_INDEX_FILE": (".git/index", ("add", "a.txt"), {"a.txt": "fx\n"}),
    "GIT_CONFIG_GLOBAL": (".gitconfig",
                          ("config", "--global", "user.name", "pwned"), None),
    "GIT_CONFIG_SYSTEM": (".gitconfig",
                          ("config", "--system", "user.name", "pwned"), None),
}


def test_the_env_vector_table_covers_the_ledger_exactly():
    """Two-way pin: a new ledger entry with no LIVE command fails here.

    Without this, adding a variable to `GIT_LOCATION_ENV` and forgetting the
    table would make `test_every_env_ledger_entry_is_a_live_write_vector`
    error on a KeyError, which reads as a broken test rather than as an
    unproven ledger entry.
    """
    assert set(_ENV_VECTORS) == set(nogit.GIT_LOCATION_ENV), (
        "the live-vector table and GIT_LOCATION_ENV disagree: "
        f"ledger-only={sorted(set(nogit.GIT_LOCATION_ENV) - set(_ENV_VECTORS))}, "
        f"table-only={sorted(set(_ENV_VECTORS) - set(nogit.GIT_LOCATION_ENV))}")


# --------------------------------------------------------------------------- #
# READS still reach the real binary
# --------------------------------------------------------------------------- #
def test_a_read_against_a_denied_repo_still_reaches_the_real_binary(
        tmp_path, no_real_git_writes):
    """The four content gates read the REAL repo with `git ls-files`.

    If the policy broke that they would scan NOTHING and pass — a silent zero
    manufactured by the guard itself, which is worse than the hazard it closes.
    """
    victim = _make_repo(tmp_path / "victim", tree={"a.txt": "x\n"})
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))

    got = _git("ls-files", cwd=victim, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"a READ was refused: {got.stderr}")

    want = subprocess.run([_real(), "ls-files"], cwd=str(victim),
                          capture_output=True, text=True, timeout=TIMEOUT)
    assert got.stdout == want.stdout
    assert got.stdout.strip(), (
        "the read returned NOTHING — a silent zero is the failure this policy "
        "must never manufacture, and an empty answer would pass an equality "
        "check against an equally-empty control")
    assert got.returncode == want.returncode


def test_status_against_a_denied_repo_does_not_rewrite_its_index(
        tmp_path, no_real_git_writes):
    """🔴 `status` REFRESHES AND REWRITES `<repo>/.git/index`.

    MEASURED against the previous revision, which had it on the read ledger and
    forwarded it untouched: the victim's index hash moved
    `63e1e299594b -> 59551aa2642b`. That is a write to a repo the test does not
    own, waved through by a ledger whose own stated membership rule forbids it.

    It is NOT refused, and that is deliberate: `status` is run against the real
    repo throughout this tree, and refusing it would be a permanently-red gate.
    The shim forwards it as `git --no-optional-locks status`, which MEASURED
    leaves the index byte-identical and produces byte-identical stdout.
    """
    import time
    victim = _victim(tmp_path / "victim")
    idx = victim / ".git" / "index"
    time.sleep(1.1)
    # Same bytes, new mtime — this is what makes git want to refresh the index.
    (victim / "keep.txt").write_text("PRECIOUS", encoding="utf-8")
    before = hashlib.sha256(idx.read_bytes()).hexdigest()

    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = _git("status", "--porcelain", cwd=victim, env=env)
    assert got.returncode == 0, f"`git status` was broken: {got.stderr!r}"
    assert hashlib.sha256(idx.read_bytes()).hexdigest() == before, (
        "`git status` rewrote the index of a repo this session denies")

    # …and the answer is still the real one.
    want = subprocess.run([_real(), "--no-optional-locks", "-C", str(victim),
                           "status", "--porcelain"], capture_output=True,
                          text=True, timeout=TIMEOUT)
    assert got.stdout == want.stdout


@pytest.mark.parametrize("args", [
    ("remote", "-v"),
    ("config", "--get", "user.name"),
    ("config", "user.name"),
    ("branch", "--show-current"),
    ("branch", "-a", "--format=%(refname:short)"),
    ("worktree", "list"),
    ("stash", "list"),
    ("rev-parse", "HEAD"),
    ("log", "--oneline", "-1"),
])
def test_dual_verb_read_forms_are_allowed_against_a_denied_repo(
        args, tmp_path, no_real_git_writes):
    """`remote`, `config`, `branch`, `worktree`, `stash` READ in these forms.

    Every one of these is run against the real repo somewhere in this tree (or
    by this policy's own containment check). Classifying the VERB alone refuses
    them all, which is a permanently-red gate; classifying the FORM is the only
    honest split.
    """
    victim = _make_repo(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = _git(*args, cwd=victim, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"`git {' '.join(args)}` is a READ but was refused:\n{got.stderr}")


def test_hash_object_without_w_still_reads_a_denied_repo(
        tmp_path, no_real_git_writes):
    """The read FORM of the verb that was wrongly ledgered as always-read."""
    victim = _victim(tmp_path / "victim")
    blob = tmp_path / "blob"
    blob.write_text("hello\n", encoding="utf-8")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = _git("hash-object", str(blob), cwd=victim, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"`git hash-object <file>` writes nothing but was refused: "
        f"{got.stderr!r}")
    assert got.stdout.strip(), "the read produced no hash at all"


# --------------------------------------------------------------------------- #
# WRITES are refused — the whole point
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("args", [
    # phase A of the incident: LOCAL porcelain, before anything was pushed
    ("commit", "--allow-empty", "-m", "c"),
    ("branch", "-m", "main", "trunk"),
    ("config", "core.bare", "true"),
    ("config", "--unset", "user.name"),
    # phase B: the remote repoint
    ("remote", "set-url", "origin", "/tmp/does-not-exist.git"),
    ("remote", "add", "evil", "/tmp/does-not-exist.git"),
    # and the rest of the write surface
    ("checkout", "-b", "x"),
    ("reset", "--hard", "HEAD"),
    ("fetch", "origin"),
    ("gc",),
    ("worktree", "prune"),
    ("tag", "-a", "v1", "-m", "x"),
    ("stash", "push"),
])
def test_a_write_to_a_repo_outside_the_allowed_roots_is_refused(
        args, tmp_path, no_real_git_writes):
    victim = _make_repo(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = _git(*args, cwd=victim, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        f"`git {' '.join(args)}` was NOT refused (rc={got.returncode}). "
        f"stdout={got.stdout!r} stderr={got.stderr!r}")
    assert nogit.BANNER in got.stderr, (
        "refused, but without this policy's banner — a firing must be "
        f"identifiable as this guard rather than as a git error: {got.stderr!r}")


def test_an_unknown_future_verb_fails_closed(tmp_path, no_real_git_writes):
    """A verb the ledger has never heard of must BLOCK, not pass.

    A blocklist fails OPEN on the next verb someone reaches for. That is not
    hypothetical: `remote set-url` was such a verb until this file existed.
    """
    victim = _make_repo(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = _git("frobnicate", "--wat", cwd=victim, env=env)
    assert got.returncode == nogit.BLOCK_EXIT
    assert nogit.BANNER in got.stderr


def test_a_git_with_no_verb_prints_usage_instead_of_refusing(
        tmp_path, no_real_git_writes):
    """`git` alone writes nothing, so refusing it is a false positive."""
    victim = _make_repo(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = subprocess.run(["git"], cwd=str(victim), env=env,
                         capture_output=True, text=True, timeout=TIMEOUT)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"a bare `git` was refused: {got.stderr!r}")
    assert nogit.BANNER not in got.stderr


@pytest.mark.parametrize("args", [
    ("--version",),
    ("--exec-path",),
    ("commit", "-h"),
    ("status", "--help"),
])
def test_help_and_version_forms_are_answered_not_refused(
        args, tmp_path, no_real_git_writes):
    """The forms that print and exit must survive the narrowed flag scan.

    Narrowing the scan to pre-verb tokens (see the `commit -m -h` measurement)
    could have made `git commit -h` a refused write. It does not, because
    `<verb> -h` as the single remaining token is handled explicitly.
    """
    victim = _make_repo(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    env["GIT_PAGER"] = "cat"
    got = _git(*args, cwd=victim, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"`git {' '.join(args)}` prints and exits but was refused: "
        f"{got.stderr!r}")


def test_a_bare_git_with_no_dash_C_is_judged_by_its_cwd(
        tmp_path, no_real_git_writes):
    """🔴 The first-class suspect.

    A `git` with neither `-C` nor an explicit `cwd=` targets whatever directory
    the process happens to be in. For a suite run from the clone, that IS the
    clone.
    """
    victim = _make_repo(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = _git("commit", "--allow-empty", "-m", "c", cwd=victim, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        "a bare `git commit` with no -C was judged by something other than "
        f"its cwd (rc={got.returncode})")
    assert nogit.BANNER in got.stderr


def test_a_path_that_spells_its_way_out_is_canonicalised(
        tmp_path, no_real_git_writes):
    """`-C <allowed>/../<denied>` must not pass a naive prefix match."""
    victim = _make_repo(tmp_path / "victim")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    env = _env(allowed=str(allowed), denied=str(victim))
    sneaky = f"{allowed}/../victim"
    got = _git("-C", sneaky, "commit", "--allow-empty", "-m", "c",
               cwd=allowed, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        f"`-C {sneaky}` resolved to the denied repo but was allowed through — "
        "the target is being compared as a STRING rather than canonicalised")


def test_a_denied_root_wins_over_an_allowed_root(tmp_path, no_real_git_writes):
    """Deny must beat allow, or the policy is inert in the nix sandbox."""
    victim = _make_repo(tmp_path / "victim")
    env = _env(allowed=str(tmp_path), denied=str(victim))
    got = _git("commit", "--allow-empty", "-m", "c", cwd=victim, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        "an explicitly DENIED repo was rescued by an enclosing allowed root")


def test_a_push_to_a_destination_outside_the_roots_is_refused(
        tmp_path, no_real_git_writes):
    """🔴 The second vector: the SOURCE repo being in-bounds is not enough."""
    src = _make_repo(tmp_path / "src")
    dest = _make_repo(tmp_path / "dest")
    subprocess.run([_real(), "remote", "add", "origin", str(dest)],
                   cwd=str(src), check=True, capture_output=True,
                   timeout=TIMEOUT)
    env = _env(allowed=str(src), denied=str(dest))
    got = _git("push", "origin", "main", cwd=src, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        f"push from an allowed repo INTO a denied one was permitted "
        f"(rc={got.returncode}): {got.stderr!r}")
    assert nogit.BANNER in got.stderr


# --------------------------------------------------------------------------- #
# 🔴 THE MEASURED BYPASSES — every one of these reached the victim
# --------------------------------------------------------------------------- #
# Each entry is a builder for the three-way harness above:
#     builder(root, victim, allowed) -> (argv_after_git, cwd, extra_env, home)
#
# NONE of these appeared anywhere in the previous 868-line battery, which is
# why it was entirely green while nine of them were live. A battery that
# enumerates only the shapes its author already knew about measures the author.

def _b_hookspath_env(root, victim, allowed):
    hooks = victim / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    write_exec(hooks / "post-commit", f"echo PWNED > {victim / 'HOOK-RAN'}\n")
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "commit", "--allow-empty", "-m", "hookprobe"],
            root,
            {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
             "GIT_CONFIG_VALUE_0": str(hooks)}, None)


def _b_hookspath_dashc(root, victim, allowed):
    hooks = victim / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    write_exec(hooks / "post-commit", f"echo PWNED > {victim / 'HOOK-RAN'}\n")
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "-c", f"core.hooksPath={hooks}", "commit",
             "--allow-empty", "-m", "hookprobe"], root, {}, None)


def _b_config_file(root, victim, allowed):
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "config", "--file",
             str(victim / ".git" / "config"), "core.bare", "true"],
            root, {}, None)


def _b_config_f(root, victim, allowed):
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "config", "-f",
             str(victim / ".git" / "config"), "core.bare", "true"],
            root, {}, None)


def _b_config_global(root, victim, allowed):
    fixture = _make_repo(allowed / "fix")
    home = victim / "home"
    home.mkdir(parents=True, exist_ok=True)
    return (["-C", str(fixture), "config", "--global", "user.name", "pwned"],
            root, {}, home)


def _push_fixture(root, victim, allowed):
    bare = allowed / "ok.git"
    subprocess.run([_real(), "init", "-q", "--bare", str(bare)],
                   check=True, capture_output=True, timeout=TIMEOUT)
    fixture = _make_repo(allowed / "fix")
    subprocess.run([_real(), "-C", str(fixture), "remote", "add", "origin",
                    str(bare)], check=True, capture_output=True,
                   timeout=TIMEOUT)
    return fixture


def _b_push_dashc_url(root, victim, allowed):
    fixture = _push_fixture(root, victim, allowed)
    return (["-C", str(fixture), "-c", f"remote.origin.url={victim}", "push",
             "origin", "HEAD:refs/heads/pwned"], root, {}, None)


def _b_push_dashc_pushurl(root, victim, allowed):
    fixture = _push_fixture(root, victim, allowed)
    return (["-C", str(fixture), "-c", f"remote.origin.pushurl={victim}",
             "push", "origin", "HEAD:refs/heads/pwned"], root, {}, None)


def _b_push_env_url(root, victim, allowed):
    fixture = _push_fixture(root, victim, allowed)
    return (["-C", str(fixture), "push", "origin", "HEAD:refs/heads/pwned"],
            root,
            {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "remote.origin.url",
             "GIT_CONFIG_VALUE_0": str(victim)}, None)


def _b_alias_git(root, victim, allowed):
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "-c",
             f"alias.x=!git -C {victim} commit --allow-empty -m ALIASPWN",
             "x"], root, {}, None)


def _b_init_separate_git_dir(root, victim, allowed):
    return (["init", "-q", "--separate-git-dir", str(victim / "stolen.git"),
             str(allowed / "newrepo")], root, {}, None)


def _b_clone_separate_git_dir(root, victim, allowed):
    src = _make_repo(allowed / "src")
    return (["clone", "-q", "--separate-git-dir", str(victim / "stolen2.git"),
             str(src), str(allowed / "cl")], root, {}, None)


def _b_worktree_add(root, victim, allowed):
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "worktree", "add", str(victim / "wt")],
            root, {}, None)


def _b_audit_mode(root, victim, allowed):
    return (["-C", str(victim), "commit", "--allow-empty", "-m", "AUDITPWN"],
            root, {nogit.MODE_ENV: "audit"}, None)


def _b_hash_object_w(root, victim, allowed):
    blob = root / "blob"
    blob.write_text("PWNBLOB\n", encoding="utf-8")
    return (["-C", str(victim), "hash-object", "-w", str(blob)], root, {},
            None)


def _b_archive_output(root, victim, allowed):
    fixture = _make_repo(allowed / "fix", tree={"a.txt": "x\n"})
    return (["-C", str(fixture), "archive", "--output",
             str(victim / "stolen.tar"), "HEAD"], root, {}, None)


def _b_bundle_create(root, victim, allowed):
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "bundle", "create",
             str(victim / "stolen.bundle"), "HEAD"], root, {}, None)


def _b_dash_h_as_a_value(root, victim, allowed):
    return (["-C", str(victim), "commit", "--allow-empty", "-m", "-h"],
            root, {}, None)


def _b_external_diff(root, victim, allowed):
    fixture = _make_repo(allowed / "fix", tree={"a.txt": "one\n"})
    (fixture / "a.txt").write_text("two\n", encoding="utf-8")
    script = root / "ed.sh"
    write_exec(script, f"echo EXTDIFF > {victim / 'EXTDIFF-RAN'}\n")
    return (["-C", str(fixture), "diff"], root,
            {"GIT_EXTERNAL_DIFF": str(script)}, None)


_BYPASSES = {
    "GIT_CONFIG_COUNT -> core.hooksPath (code execution)": _b_hookspath_env,
    "-c core.hooksPath=<victim> (code execution)": _b_hookspath_dashc,
    "config --file <victim>/.git/config": _b_config_file,
    "config -f <victim>/.git/config": _b_config_f,
    "config --global escapes into $HOME": _b_config_global,
    "-c remote.origin.url=<victim> push": _b_push_dashc_url,
    "-c remote.origin.pushurl=<victim> push": _b_push_dashc_pushurl,
    "GIT_CONFIG_COUNT -> remote.origin.url push": _b_push_env_url,
    "-c alias.x=!git -C <victim> commit (PATH escape)": _b_alias_git,
    "init --separate-git-dir <victim>/x": _b_init_separate_git_dir,
    "clone --separate-git-dir <victim>/x": _b_clone_separate_git_dir,
    "worktree add <victim>/wt": _b_worktree_add,
    "DEVRC_TEST_GIT_MODE=audit disarms the guard": _b_audit_mode,
    "hash-object -w into a denied repo": _b_hash_object_w,
    "archive --output=<victim>/x.tar": _b_archive_output,
    "bundle create <victim>/x.bundle": _b_bundle_create,
    "commit -m -h (whole-argv read-flag scan)": _b_dash_h_as_a_value,
    "GIT_EXTERNAL_DIFF turns a read into execution": _b_external_diff,
}


# --------------------------------------------------------------------------- #
# 🔴 ROUND TWO — the SAME capabilities, spelled differently
# --------------------------------------------------------------------------- #
# Every one of these is a second spelling of a capability the first round
# already closed, and each was found by an independent audit rather than by
# this file's author. That is the whole lesson: enumerating the argv shape that
# was demonstrated leaves every other shape of the same capability open, so
# these are here to keep the STRUCTURAL fixes honest — the flag twin of an env
# variable, the relative twin of an absolute path, the read-flag-first twin of
# a bare write, and a numeric gate that does not parse what git parses.


def _b_worktree_flag(root, victim, allowed):
    """`--work-tree=` — the COMMAND-LINE twin of `GIT_WORK_TREE`.

    Measured on a guard that closed the env variable and skipped the flag:
    `git --work-tree=<victim> clean -fdx` DELETED EVERY FILE in the victim's
    working tree.
    """
    fixture = _make_repo(allowed / "fix")
    return (["--work-tree=" + str(victim), "-C", str(fixture), "clean", "-fdx"],
            root, {}, None)


def _b_gitdir_flag(root, victim, allowed):
    """`--git-dir=` — the command-line twin of `GIT_DIR`."""
    fixture = _make_repo(allowed / "fix")
    return (["--git-dir=" + str(victim / ".git"), "-C", str(fixture), "commit",
             "--allow-empty", "-m", "GD"], root, {}, None)


def _b_gitdir_victim_worktree_allowed(root, victim, allowed):
    """🔴 BOTH flags set, only one of them out of bounds.

    The `top` chain picks work-tree over git-dir, so a guard that judges the
    single resolved `top` sees only the innocent half. The check has to be over
    the SET.
    """
    fixture = _make_repo(allowed / "fix")
    return (["--git-dir=" + str(victim / ".git"),
             "--work-tree=" + str(fixture), "-C", str(fixture),
             "commit", "--allow-empty", "-m", "SPLIT"], root, {}, None)


def _b_read_flag_first(root, victim, allowed):
    """A read-shaped flag placed FIRST must not license the rest of the argv."""
    return (["-C", str(victim), "branch", "--format=%(refname)", "-m", "main",
             "trunk"], root, {}, None)


def _b_symbolic_ref_write(root, victim, allowed):
    """`symbolic-ref <name> <ref>` REPOINTS — the verb was on the READ ledger."""
    subprocess.run([_real(), "-C", str(victim), "branch", "trunk"],
                   capture_output=True, timeout=TIMEOUT)
    return (["-C", str(victim), "symbolic-ref", "-q", "HEAD",
             "refs/heads/trunk"], root, {}, None)


def _hookdir(victim):
    hooks = victim / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    write_exec(hooks / "post-commit", f"echo PWNED > {victim / 'HOOK-RAN'}\n")
    return hooks


def _b_config_count_plus(root, victim, allowed):
    """`GIT_CONFIG_COUNT=+1` — git's `strtoumax` accepts it; `[0-9]*` does not."""
    hooks = _hookdir(victim)
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "commit", "--allow-empty", "-m", "p"], root,
            {"GIT_CONFIG_COUNT": "+1", "GIT_CONFIG_KEY_0": "core.hooksPath",
             "GIT_CONFIG_VALUE_0": str(hooks)}, None)


def _b_config_count_space(root, victim, allowed):
    """`GIT_CONFIG_COUNT=' 1'` — leading whitespace, same story."""
    hooks = _hookdir(victim)
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "commit", "--allow-empty", "-m", "p"], root,
            {"GIT_CONFIG_COUNT": " 1", "GIT_CONFIG_KEY_0": "core.hooksPath",
             "GIT_CONFIG_VALUE_0": str(hooks)}, None)


def _b_diff_external_dashc(root, victim, allowed):
    """`-c diff.external=` on a READ — the scan must run above the fast path."""
    fixture = _make_repo(allowed / "fix", tree={"a.txt": "one\n"})
    (fixture / "a.txt").write_text("two\n", encoding="utf-8")
    script = root / "ed.sh"
    write_exec(script, f"echo X > {victim / 'EXT-RAN'}\n")
    return (["-C", str(fixture), "-c", f"diff.external={script}", "diff"],
            root, {}, None)


def _b_filter_clean(root, victim, allowed):
    """A clean filter is a child-injection channel like an alias."""
    fixture = _make_repo(allowed / "fix")
    (fixture / ".gitattributes").write_text("*.q filter=x\n", encoding="utf-8")
    (fixture / "z.q").write_text("data\n", encoding="utf-8")
    script = root / "cl.sh"
    write_exec(script,
               f"git -C {victim} commit --allow-empty -m CLEAN\ncat\n")
    return (["-C", str(fixture), "-c", f"filter.x.clean={script}", "add", "-A"],
            root, {}, None)


def _b_rel_config_file(root, victim, allowed):
    """A RELATIVE `--file` — an absolute-only matcher is relative-walkable."""
    fixture = _make_repo(allowed / "fix")
    rel = os.path.relpath(victim / ".git" / "config", fixture)
    return (["-C", str(fixture), "config", "--file", rel, "core.bare", "true"],
            root, {}, None)


def _b_rel_worktree_add(root, victim, allowed):
    fixture = _make_repo(allowed / "fix")
    rel = os.path.relpath(victim / "wt", fixture)
    return (["-C", str(fixture), "worktree", "add", rel], root, {}, None)


def _b_rel_separate_git_dir(root, victim, allowed):
    """🔴 Also pins that a relative operand resolves against the CWD.

    `base` is reassigned to the path `init` will create, so anchoring relative
    operands there would check one directory away from where the bytes land.
    """
    newrepo = allowed / "newrepo"
    newrepo.mkdir(parents=True, exist_ok=True)
    rel = os.path.relpath(victim / "stolen.git", newrepo)
    return (["-C", str(newrepo), "init", "-q", "--separate-git-dir=" + rel,
             "."], root, {}, None)


def _b_rel_separate_git_dir_nested(root, victim, allowed):
    """🔴 THE CASE THAT SEPARATES `cwdbase` FROM `base`.

    `_b_rel_separate_git_dir` uses `init … .`, where the reassigned `base`
    equals the cwd and the two anchors agree — so it cannot tell them apart. A
    mutation sweep caught that: anchoring relative operands to `base` SURVIVED
    the whole battery.

    Here `init` is given a SUBDIRECTORY, so `base` becomes `<cwd>/newrepo`
    while git still resolves `../victim/...` against `<cwd>`. Anchored to
    `base`, `../victim/stolen.git` reads as `<allowed>/victim/stolen.git` —
    INSIDE the allowed root, so the call is permitted — while the bytes land in
    `<root>/victim/stolen.git`. Anchored to `cwdbase` it resolves where git
    puts it and is refused.
    """
    return (["-C", str(allowed), "init", "-q",
             "--separate-git-dir=../victim/stolen.git", "newrepo"],
            root, {}, None)


def _b_rel_clone(root, victim, allowed):
    src = _make_repo(allowed / "src")
    fixture = allowed / "fix"
    fixture.mkdir(parents=True, exist_ok=True)
    rel = os.path.relpath(victim / "cl", fixture)
    return (["-C", str(fixture), "clone", "-q", str(src), rel], root, {}, None)


def _b_unresolvable_dotdot_init(root, victim, allowed):
    """🔴 An UNRESOLVABLE path with `..` — protected-unknown, not protected-no.

    Both `cd`s in `canon_of` fail because `does-not-exist` is not there, so the
    raw string came back — and the raw string starts with `<allowed>/`, so it
    matched an allowed root while `realpath` puts it inside the victim.

    MEASURED: `git init -q <allowed>/does-not-exist/deeper/../../../victim/
    planted` exited 0 and created `<victim>/planted`. Same for `clone`.
    """
    return (["init", "-q",
             str(allowed) + "/does-not-exist/deeper/../../../victim/planted"],
            root, {}, None)


def _b_unresolvable_dotdot_clone(root, victim, allowed):
    src = _make_repo(allowed / "src")
    return (["clone", "-q", str(src),
             str(allowed) + "/does-not-exist/deeper/../../../victim/planted"],
            root, {}, None)


def _b_archive_over_config(root, victim, allowed):
    """`archive --output=` aimed at the victim's own config file."""
    fixture = _make_repo(allowed / "fix", tree={"a.txt": "x\n"})
    return (["-C", str(fixture), "archive", "--format=tar",
             "--output=" + str(victim / ".git" / "config"), "HEAD"],
            root, {}, None)


def _b_init_into_victim(root, victim, allowed):
    return (["init", "-q", str(victim / "newone")], root, {}, None)


def _b_cumulative_dash_C(root, victim, allowed):
    """🔴 git applies multiple `-C` CUMULATIVELY; keeping only the last one
    resolves `.` against the shim's cwd and lands in the victim."""
    return (["-C", str(victim), "-C", ".", "commit", "--allow-empty", "-m",
             "CC"], root, {}, None)


def _b_cumulative_dash_C_relative(root, victim, allowed):
    fixture = _make_repo(allowed / "fix")
    return (["-C", str(fixture), "-C", os.path.relpath(victim, fixture),
             "commit", "--allow-empty", "-m", "CC2"], root, {}, None)


def _b_bisect_run(root, victim, allowed):
    """`bisect run <cmd>` executes arbitrary code once per step.

    The command's own `git` is what the exec-path farm catches; this needs a
    real bisect range or the script never runs at all and the case is vacuous.
    """
    fixture = _make_repo(allowed / "fix")
    for i in range(4):
        subprocess.run([_real(), "-C", str(fixture), "commit", "-q",
                        "--allow-empty", "-m", f"c{i}"], check=True,
                       capture_output=True, timeout=TIMEOUT)
    head = subprocess.run([_real(), "-C", str(fixture), "rev-parse", "HEAD"],
                          capture_output=True, text=True,
                          timeout=TIMEOUT).stdout.strip()
    first = subprocess.run(
        [_real(), "-C", str(fixture), "rev-list", "--max-parents=0", "HEAD"],
        capture_output=True, text=True, timeout=TIMEOUT).stdout.strip()
    subprocess.run([_real(), "-C", str(fixture), "bisect", "start", head,
                    first], capture_output=True, timeout=TIMEOUT)
    script = root / "bis.sh"
    write_exec(script,
               f"git -C {victim} commit --allow-empty -m BISECT\nexit 1\n")
    return (["-C", str(fixture), "bisect", "run", str(script)], root, {}, None)


_BYPASSES.update({
    "--work-tree=<victim> clean -fdx": _b_worktree_flag,
    "--git-dir=<victim>/.git commit": _b_gitdir_flag,
    "--git-dir=<victim> with an allowed --work-tree": _b_gitdir_victim_worktree_allowed,
    "branch --format=... -m main trunk (read flag first)": _b_read_flag_first,
    "symbolic-ref -q HEAD <ref> (was on the read ledger)": _b_symbolic_ref_write,
    "GIT_CONFIG_COUNT=+1 -> core.hooksPath": _b_config_count_plus,
    "GIT_CONFIG_COUNT=' 1' -> core.hooksPath": _b_config_count_space,
    "-c diff.external=<script> on a read": _b_diff_external_dashc,
    "-c filter.x.clean=<git-into-victim> add": _b_filter_clean,
    "config --file ../victim/.git/config (relative)": _b_rel_config_file,
    "worktree add ../victim/wt (relative)": _b_rel_worktree_add,
    "init --separate-git-dir=../victim/x (relative)": _b_rel_separate_git_dir,
    "init --separate-git-dir=../victim/x from a NESTED base":
        _b_rel_separate_git_dir_nested,
    "clone into ../victim/cl (relative)": _b_rel_clone,
    "archive --output=<victim>/.git/config": _b_archive_over_config,
    "init <victim>/newone": _b_init_into_victim,
    "init <allowed>/nonexistent/../../victim/x (unresolvable)":
        _b_unresolvable_dotdot_init,
    "clone into <allowed>/nonexistent/../../victim/x (unresolvable)":
        _b_unresolvable_dotdot_clone,
    "-C <victim> -C . commit (cumulative -C)": _b_cumulative_dash_C,
    "-C <allowed> -C ../victim commit (cumulative -C)": _b_cumulative_dash_C_relative,
    "bisect run <git-into-victim>": _b_bisect_run,
})


@pytest.mark.parametrize("args", [
    ("notes",),                                  # bare = list
    ("fsck",),                                   # inspects, writes nothing
    ("symbolic-ref", "HEAD"),                    # one positional = read
    ("branch", "--format=%(refname)"),           # list form
    ("interpret-trailers", "--only-input"),      # writes to stdout
])
def test_a_read_FORM_of_a_dual_verb_is_not_refused(
        args, tmp_path, no_real_git_writes):
    """🔴 THE OTHER HALF OF EVERY LEDGER MOVE.

    `symbolic-ref`, `interpret-trailers`, `hash-object` and `fsck` were all
    moved off the read ledger or onto the dual list in this rework, each
    because one FORM of the verb writes. Moving a verb is only correct if its
    reading forms still pass — otherwise the fix trades a false negative for a
    permanently-red gate, and a permanently-red gate gets switched off.

    MEASURED on the first attempt: refusing `fsck` outright was exactly that
    mistake, and it is why the verb is DUAL rather than simply absent.
    """
    victim = _make_repo(tmp_path / "victim", tree={"a.txt": "x\n"})
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = _git(*args, cwd=victim, env=env, stdin_text="")
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"`git {' '.join(args)}` is a READING form of a dual verb but was "
        f"refused:\n{got.stderr}")


def test_a_one_argument_clone_targets_the_cwd_not_the_source(
        tmp_path, no_real_git_writes):
    """`git clone <protected>` clones INTO the cwd; the source is only read.

    A destination loop that takes "the last positional" reads the SOURCE for a
    one-argument clone and refuses a legitimate read of a protected repo —
    which is how a guard ends up unable to clone the tree under test into a
    fixture, a thing several tests in this tree do on purpose.
    """
    victim = _make_repo(tmp_path / "victim", tree={"a.txt": "x\n"})
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    env = _env(allowed=str(allowed), denied=str(victim))
    got = _git("clone", "-q", str(victim), cwd=allowed, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"a one-argument `git clone <denied>` into an allowed cwd was refused "
        f"— the destination was read as the SOURCE: {got.stderr!r}")
    assert (allowed / "victim" / ".git").is_dir()


# 🔴 Hazards closed by CONTAINING the write rather than by refusing the outer
# command. `bisect run` legitimately succeeds — what must not happen is the
# `git` inside its script reaching the victim, and that inner call is refused
# by the exec-path farm while the bisect itself completes rc 0. Demanding a
# banner from the OUTER command here would be asserting the wrong thing, in
# the same way it would be for `status --no-optional-locks`.
_CLOSED_BY_CONTAINMENT = {"bisect run <git-into-victim>"}


@pytest.mark.parametrize("name", sorted(_BYPASSES))
def test_a_measured_bypass_is_closed(name, tmp_path, no_real_git_writes):
    """Every one of these exited 0 and reached the victim before this rework.

    Scored by the three-way harness: the probe must be clean, the vector must
    be LIVE with the guard removed, and the guard must hold. A case that stops
    being live fails here rather than quietly becoming decoration.
    """
    if name in _CLOSED_BY_CONTAINMENT:
        _expect_closed(tmp_path, _BYPASSES[name], name=name)
    else:
        _expect_refused(tmp_path, _BYPASSES[name], name=name)


def test_the_containment_only_set_is_not_a_place_to_hide_a_failure():
    """🔴 An escape hatch that can grow silently is not an escape hatch.

    `_CLOSED_BY_CONTAINMENT` downgrades a case from "refused with this policy's
    banner" to "the victim did not move". That is the right assertion for a
    command whose own success is legitimate — and it is also exactly how a
    genuine regression would be papered over. The set is pinned, so adding to
    it is a reviewed edit.
    """
    assert _CLOSED_BY_CONTAINMENT == {"bisect run <git-into-victim>"}
    assert _CLOSED_BY_CONTAINMENT <= set(_BYPASSES), (
        "a name in the containment-only set is not a case any more")


def test_an_audit_mode_env_var_cannot_disarm_the_guard(
        tmp_path, no_real_git_writes):
    """The LIVE half of the audit-mode fix, stated separately.

    `test_the_shim_has_no_env_readable_mode` is a claim about the generated
    text. This one runs the command.
    """
    victim = _victim(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    env[nogit.MODE_ENV] = nogit.MODE_AUDIT
    before = _victim_state(victim)
    got = _git("commit", "--allow-empty", "-m", "AUDITPWN", cwd=victim,
               env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        f"{nogit.MODE_ENV}={nogit.MODE_AUDIT} disarmed the guard "
        f"(rc={got.returncode})")
    assert _victim_state(victim) == before


def test_audit_mode_still_exists_as_a_bake_time_argument(tmp_path):
    """The measuring tool is not deleted, only made unreachable from the env.

    Audit mode is how this policy's blast radius was established before it was
    turned on. Keeping it available to `install()` while removing the env
    switch is the whole point of the change, so both halves are pinned.
    """
    stub = tmp_path / "stub"
    _install(stub, allowed=str(tmp_path / "nowhere"),
                  denied=str(tmp_path), mode=nogit.MODE_AUDIT)
    answer = _handshake(stub)
    assert answer is not None and answer["audit"] == "1", (
        "install(mode=MODE_AUDIT) did not produce an audit-mode shim")

    stub2 = tmp_path / "stub2"
    _install(stub2, allowed=str(tmp_path / "nowhere"),
                  denied=str(tmp_path))
    answer2 = _handshake(stub2)
    assert answer2 is not None and answer2["audit"] == "0", (
        "the DEFAULT install is not block mode")


# --------------------------------------------------------------------------- #
# 🔴 THE PATH ESCAPE — git's own libexec, and what the farm does about it
# --------------------------------------------------------------------------- #
def test_git_prepends_its_own_exec_path_to_PATH_for_children():
    """The measurement that falsified this policy's central claim.

    The previous revision asserted the shim was reached "at any depth" because
    it was first on PATH. It is not: git PREPENDS its exec-path for every child
    it spawns, and that directory ships a real `git`. This pins the mechanism
    itself, so the claim cannot be restated without the measurement going with
    it.
    """
    real = _real()
    # 🔴 ASKED WITH L2 OFF AND `GIT_EXEC_PATH` CLEARED. Through the shim, git
    # reports the FARM as its exec-path (which is the whole point of the farm)
    # — so a naive query here answers with the guard's own directory and the
    # comparison below silently compares the farm against the libexec.
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    clean["PATH"] = _real_path()
    with _l2_off():
        exec_path = subprocess.run([real, "--exec-path"], env=clean,
                                   capture_output=True, text=True,
                                   timeout=TIMEOUT).stdout.strip()
    assert (Path(exec_path) / "git").exists(), (
        f"{exec_path} has no `git`, so this host's git cannot exhibit the "
        "escape — the farm may be unnecessary here, which is worth knowing")

    env = dict(clean)
    with _l2_off():
        out = subprocess.run(
            [real, "-c", "alias.p=!echo PATH0=${PATH%%:*}", "p"],
            cwd="/", env=env, capture_output=True, text=True, timeout=TIMEOUT)
    assert f"PATH0={exec_path}" in out.stdout, (
        "git did not prepend its exec-path for the alias child; the escape "
        f"this policy's farm closes may not exist here. stdout={out.stdout!r}")


@pytest.mark.parametrize("where", ["own-config", "own-hook"])
def test_a_git_spawned_child_resolves_git_to_the_shim(
        where, tmp_path, no_real_git_writes):
    """🔴 The escape closed for code that is ALREADY IN THE REPO.

    Refusing the INJECTION channel (`-c alias.*=!…`) is not the same as closing
    the escape: an alias or hook already present in a fixture's own
    `.git/config` names nothing on the command line and nothing in the
    environment, so no injection check can see it.

    `GIT_EXEC_PATH` closes it structurally instead — git prepends the farm, and
    `git` in the farm is the shim. Measured, both of these wrote the victim a
    commit before the farm existed and are refused with it.
    """
    root = tmp_path / "w"
    root.mkdir()
    victim = _victim(root / "victim")
    allowed = root / "allowed"
    allowed.mkdir()
    fixture = _make_repo(allowed / "fix")
    inner = f"git -C {victim} commit --allow-empty -m CHILDPWN"
    if where == "own-config":
        subprocess.run([_real(), "-C", str(fixture), "config", "alias.x",
                        f"!{inner}"], check=True, capture_output=True,
                       timeout=TIMEOUT)
        argv = ["-C", str(fixture), "x"]
    else:
        hook = fixture / ".git" / "hooks" / "post-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        write_exec(hook, f"{inner}\n")
        argv = ["-C", str(fixture), "commit", "--allow-empty", "-m", "trigger"]

    env = _env(allowed=str(allowed), denied=str(victim))
    env["HOME"] = str(root / "home")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)

    # LIVE control: the same thing with the guard removed must reach the victim.
    un_env = dict(env)
    un_env["PATH"] = _real_path()
    un_env.pop("GIT_EXEC_PATH", None)
    un_root = tmp_path / "u"
    un_root.mkdir()
    u_victim = _victim(un_root / "victim")
    u_allowed = un_root / "allowed"
    u_allowed.mkdir()
    u_fixture = _make_repo(u_allowed / "fix")
    u_inner = f"git -C {u_victim} commit --allow-empty -m CHILDPWN"
    if where == "own-config":
        subprocess.run([_real(), "-C", str(u_fixture), "config", "alias.x",
                        f"!{u_inner}"], check=True, capture_output=True,
                       timeout=TIMEOUT)
        u_argv = ["-C", str(u_fixture), "x"]
    else:
        h = u_fixture / ".git" / "hooks" / "post-commit"
        h.parent.mkdir(parents=True, exist_ok=True)
        write_exec(h, f"{u_inner}\n")
        u_argv = ["-C", str(u_fixture), "commit", "--allow-empty", "-m",
                  "trigger"]
    u_env = _env(allowed=str(u_allowed), denied=str(u_victim),
                 path=_real_path())
    u_env["HOME"] = str(un_root / "home")
    Path(u_env["HOME"]).mkdir(parents=True, exist_ok=True)
    u_before = _victim_state(u_victim)
    with _l2_off():
        subprocess.run([_real(), *u_argv], cwd=str(un_root), env=u_env,
                       capture_output=True, text=True, timeout=TIMEOUT)
    assert _victim_state(u_victim) != u_before, (
        f"the {where} escape is not LIVE on this host, so the guarded result "
        "below proves nothing")

    before = _victim_state(victim)
    _git(*argv, cwd=root, env=env)
    assert _victim_state(victim) == before, (
        f"a git spawned from a {where} reached the victim — GIT_EXEC_PATH is "
        "not shadowing `git` for git's own children")


def test_a_shell_alias_writing_the_victim_is_NOT_covered(
        tmp_path, no_real_git_writes):
    """🔴 THE STATED LIMIT, PINNED AS A MEASUREMENT RATHER THAN A SENTENCE.

    The farm makes every `git` a git-spawned child runs reach the shim. It does
    NOT — and no git guard can — bound a shell command that writes a file
    directly. Measured: an alias whose body is `sh -c 'echo … > <victim>/X'`
    creates that file with the guard fully installed.

    This is pinned so that the limit is a fact the suite carries rather than a
    claim in a docstring. If a future change DOES cover it, this test goes red
    and the docstring must be rewritten — which is the correct outcome, not a
    nuisance.

    EXPOSED SURFACE, enumerated — and enumerated by what actually happens to
    each channel, because a docstring that names a channel the code does not
    refuse is the thing this rework kept finding:

      * REFUSED outright when non-empty, so the channel cannot be opened:
        `alias.*`, `core.fsmonitor`, `diff.external`, `filter.*.clean` /
        `.smudge` / `.process`, `*.textconv`, `core.pager`, `core.editor`,
        `core.sshCommand`, `credential.helper`, `gpg.program`,
        `merge.*.driver` (`GIT_CONFIG_CMD_KEYS`), and the environment twins
        `GIT_EXTERNAL_DIFF`, `GIT_ASKPASS`, `GIT_PROXY_COMMAND`, `GIT_SSH`
        (`GIT_EXEC_ENV`).
      * BOUNDED rather than refused, because a real caller needs them:
        `core.hooksPath` and `init.templateDir` (`GIT_CONFIG_PATH_KEYS`) — the
        value must be inside an allowed root.
      * NOT REFUSED AT ALL, and named here so the list is honest:
        `GIT_SSH_COMMAND` (one legitimate caller —
        `scripts/resume-state.sh:327` — and putting it on the list cost nine
        red tests), and `.git/hooks/*` scripts, `rebase --exec` and
        `submodule foreach`, whose bodies are argv or on-disk files rather
        than an injection channel.

    For everything in the last group, and for anything in the first two that a
    repo the test OWNS already carries in its own config, the `git` such code
    spawns is still caught by the exec-path farm — measured, in
    `test_a_git_spawned_child_resolves_git_to_the_shim`. What is uncovered is
    the part of such a body that is not a `git` invocation, which is what this
    test pins.
    """
    root = tmp_path / "w"
    root.mkdir()
    victim = _victim(root / "victim")
    allowed = root / "allowed"
    allowed.mkdir()
    fixture = _make_repo(allowed / "fix")
    marker = victim / "SHELL-ALIAS-RAN"
    subprocess.run(
        [_real(), "-C", str(fixture), "config", "alias.x",
         f"!sh -c 'echo OWNED > {marker}'"],
        check=True, capture_output=True, timeout=TIMEOUT)

    env = _env(allowed=str(allowed), denied=str(victim))
    env["HOME"] = str(root / "home")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    _git("-C", str(fixture), "x", cwd=root, env=env)
    assert marker.exists(), (
        "a shell alias writing the victim directly was BLOCKED. That is a "
        "better outcome than this test expects — update this test and the "
        "'stated limit' paragraphs in testlib/nogit.py and "
        "testlib/nogit_plugin.py, which currently tell the reader it is not "
        "covered.")


# --------------------------------------------------------------------------- #
# POSITIVE CONTROL — legitimate fixture git must keep working
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("args", [
    ("commit", "--allow-empty", "-m", "c"),
    ("branch", "-m", "main", "trunk"),
    ("config", "core.bare", "false"),
    ("remote", "add", "origin", "/tmp/whatever.git"),
    ("remote", "set-url", "origin", "/tmp/other.git"),
    ("checkout", "-b", "feature"),
    ("tag", "-a", "v1", "-m", "x"),
])
def test_a_write_inside_an_allowed_root_still_works(
        args, tmp_path, no_real_git_writes):
    """🔴 A guard that refuses everything looks identical to one that works.

    Every negative assertion above is satisfied by a shim that blocks
    unconditionally. This is the half that distinguishes them, and it is the
    half that found two real defects: the first draft refused `git init
    <tmpdir>` and refused every fixture-to-fixture `push`.
    """
    pre = "/tmp/original.git" if args[:2] == ("remote", "set-url") else None
    fixture = _make_repo(tmp_path / "fixture", origin=pre)
    env = _env(allowed=str(tmp_path), denied=str(tmp_path / "nowhere"))
    got = _git(*args, cwd=fixture, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"`git {' '.join(args)}` inside the test's OWN tmpdir was refused. "
        f"stderr={got.stderr!r}")
    assert got.returncode == 0, f"unexpected git failure: {got.stderr!r}"


def test_the_analyze_service_index_hook_neutraliser_still_works(
        tmp_path, no_real_git_writes):
    """🔴 THE CALLER THAT FORCED A BOUNDS CHECK RATHER THAN A REFUSAL.

    `scripts/analyze-service-index/commit.sh:247` exports, for every git call
    it makes:

        GIT_CONFIG_NOSYSTEM=1
        GIT_CONFIG_GLOBAL=/dev/null
        GIT_CONFIG_COUNT=2
        GIT_CONFIG_KEY_0=core.hooksPath   GIT_CONFIG_VALUE_0=$ASI_NOHOOKS
        GIT_CONFIG_KEY_1=init.templateDir GIT_CONFIG_VALUE_1=$ASI_NOHOOKS

    where `$ASI_NOHOOKS` is an empty `mktemp -d` under `$TMPDIR`. It is doing
    the RIGHT thing — neutralising hooks — and two tests pin it. Blanket-
    refusing the `GIT_CONFIG_COUNT` family would have gone red on it and the
    guard would have been switched off.

    This reproduces the exact shape and requires it to pass, which is what
    makes `test_a_measured_bypass_is_closed[GIT_CONFIG_COUNT -> core.hooksPath …]`
    a statement about the VALUE rather than about the mechanism.
    """
    nohooks = tmp_path / "asi-nohooks"
    nohooks.mkdir()
    fixture = _make_repo(tmp_path / "fix")
    env = _env(allowed=str(tmp_path), denied=str(tmp_path / "nowhere"))
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(nohooks),
        "GIT_CONFIG_KEY_1": "init.templateDir",
        "GIT_CONFIG_VALUE_1": str(nohooks),
    })
    got = _git("commit", "--allow-empty", "-m", "asi", cwd=fixture, env=env)
    assert got.returncode == 0, (
        "the analyze-service-index hook-neutralising idiom was refused "
        f"(rc={got.returncode}): {got.stderr!r}. A bounds check on the VALUE "
        "must pass a path inside an allowed root.")


def test_an_empty_command_valued_config_is_a_disarm_not_an_arm(
        tmp_path, no_real_git_writes):
    """`-c core.fsmonitor=` DISABLES the hook — `task-spec-drafter` relies on it.

    `scripts/task-spec-drafter/ticket-status:249` passes exactly this as a
    hardening measure. Refusing every command-valued key regardless of value
    would have gone red on a caller doing the right thing, so an EMPTY value is
    explicitly allowed.
    """
    fixture = _make_repo(tmp_path / "fix")
    env = _env(allowed=str(tmp_path), denied=str(tmp_path / "nowhere"))
    got = _git("-c", "core.fsmonitor=", "-c", "protocol.ext.allow=never",
               "commit", "--allow-empty", "-m", "hardened", cwd=fixture,
               env=env)
    assert got.returncode == 0, (
        f"`-c core.fsmonitor=` (empty = disarm) was refused: {got.stderr!r}")


def test_a_nonempty_command_valued_config_is_refused(
        tmp_path, no_real_git_writes):
    """The mirror of the above — and the reason the empty case is not a hole."""
    fixture = _make_repo(tmp_path / "fix")
    env = _env(allowed=str(tmp_path), denied=str(tmp_path / "nowhere"))
    got = _git("-c", "core.fsmonitor=/bin/true", "commit", "--allow-empty",
               "-m", "armed", cwd=fixture, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        f"`-c core.fsmonitor=<command>` was allowed (rc={got.returncode}) — a "
        "command string cannot be bounded to a directory, so it must be "
        "refused")
    assert nogit.BANNER in got.stderr


def test_git_init_of_a_fixture_dir_is_allowed_from_inside_a_denied_cwd(
        tmp_path, no_real_git_writes):
    """`git init <tmpdir>` writes to the ARGUMENT, not to the cwd."""
    denied = _make_repo(tmp_path / "denied")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    env = _env(allowed=str(allowed), denied=str(denied))
    got = _git("init", "-q", str(allowed / "new"), cwd=denied, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"`git init <allowed>` from a denied cwd was refused: {got.stderr!r}")
    assert (allowed / "new" / ".git").is_dir()


def test_a_separate_git_dir_inside_an_allowed_root_still_works(
        tmp_path, no_real_git_writes):
    """The positive half of the `--separate-git-dir` destination check."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    env = _env(allowed=str(allowed), denied=str(tmp_path / "nowhere"))
    got = _git("init", "-q", "--separate-git-dir", str(allowed / "gd"),
               str(allowed / "wt"), cwd=allowed, env=env)
    assert got.returncode == 0, (
        f"`--separate-git-dir` inside an allowed root was refused: "
        f"{got.stderr!r}")
    assert (allowed / "gd" / "HEAD").exists()


def test_a_worktree_add_inside_an_allowed_root_still_works(
        tmp_path, no_real_git_writes):
    """The positive half of the `worktree add` destination check."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    fixture = _make_repo(allowed / "fix")
    env = _env(allowed=str(allowed), denied=str(tmp_path / "nowhere"))
    got = _git("-C", str(fixture), "worktree", "add", str(allowed / "wt"),
               cwd=allowed, env=env)
    assert got.returncode == 0, (
        f"`worktree add` inside an allowed root was refused: {got.stderr!r}")
    assert (allowed / "wt" / ".git").exists()


def test_an_archive_output_inside_an_allowed_root_still_works(
        tmp_path, no_real_git_writes):
    """The positive half of the `archive --output` destination check."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    fixture = _make_repo(allowed / "fix", tree={"a.txt": "x\n"})
    env = _env(allowed=str(allowed), denied=str(tmp_path / "nowhere"))
    got = _git("-C", str(fixture), "archive", "--output",
               str(allowed / "ok.tar"), "HEAD", cwd=allowed, env=env)
    assert got.returncode == 0, (
        f"`archive --output` inside an allowed root was refused: "
        f"{got.stderr!r}")
    assert (allowed / "ok.tar").exists()


def test_git_clone_into_a_denied_path_is_still_refused(
        tmp_path, no_real_git_writes):
    """The positional target is CHECKED, not trusted."""
    src = _make_repo(tmp_path / "src")
    denied = tmp_path / "denied"
    denied.mkdir()
    env = _env(allowed=str(tmp_path / "allowed"), denied=str(denied))
    got = _git("clone", "-q", str(src), str(denied / "here"), cwd=src, env=env)
    assert got.returncode == nogit.BLOCK_EXIT
    assert not (denied / "here").exists()


# --------------------------------------------------------------------------- #
# THE RECONSTRUCTION — a repoint that is put back afterwards
# --------------------------------------------------------------------------- #
def test_a_restore_afterwards_does_not_hide_the_write(
        tmp_path, no_real_git_writes):
    """🔴 Reproduce the reported shape: repoint `origin`, work, restore it.

    The restore is why this was invisible — after the run `git remote -v` on
    the real clone reported exactly the right URL. A guard that inspected state
    AFTER the fact would see nothing at all.
    """
    victim = _make_repo(tmp_path / "victim")
    good = str(tmp_path / "upstream.git")
    subprocess.run([_real(), "remote", "add", "origin", good],
                   cwd=str(victim), check=True, capture_output=True,
                   timeout=TIMEOUT)

    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    script = tmp_path / "victim.sh"
    # The drift-check.sh:444 / ship.sh:190 shape: `:-` falls back on EMPTY as
    # well as unset, so an override that is set-but-blank lands on the default.
    write_exec(
        script,
        'repo="${DRIFT_REPO:-' + str(victim) + '}"\n'
        'saved=$(git -C "$repo" remote get-url origin)\n'
        'git -C "$repo" remote set-url origin /tmp/poisoned.git || exit 40\n'
        'git -C "$repo" remote set-url origin "$saved"\n'
        'echo restored\n')

    env["DRIFT_REPO"] = ""  # present but EMPTY — the case `:-` hides
    got = subprocess.run(["/bin/sh", str(script)], cwd=str(tmp_path), env=env,
                         capture_output=True, text=True, timeout=TIMEOUT)

    assert got.returncode == 40, (
        "the repoint was not refused — the script ran to completion and would "
        f"have restored the URL, hiding it (rc={got.returncode})")
    assert nogit.BANNER in got.stderr
    assert "restored" not in got.stdout

    after = subprocess.run([_real(), "remote", "get-url", "origin"],
                           cwd=str(victim), capture_output=True, text=True,
                           timeout=TIMEOUT)
    assert after.stdout.strip() == good, (
        "origin is not what it was — the write landed")


# --------------------------------------------------------------------------- #
# 🔴 THE GIT ENVIRONMENT — `-C` IS NOT PROTECTIVE
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("var", sorted(_ENV_VECTORS))
def test_a_git_env_var_armed_at_a_victim_is_refused(
        var, tmp_path, no_real_git_writes):
    """🔴 Every path ARGUMENT names an innocent fixture; only the ENV points out.

    Measured before this check existed:

        GIT_DIR=<victim>/.git git -C <fixture> commit --allow-empty -m PWNED
        -> rc 0, and the victim's commit count went 1 -> 2.

    while `git -C <fixture> rev-parse --show-toplevel` answered `<fixture>`.
    That is why "absolute `-C`, therefore fixture-scoped" cleared this suite in
    two independent audits: it is the wrong property.

    The command paired with each variable is the one proven LIVE by
    `test_every_env_ledger_entry_is_a_live_write_vector`, so "the victim is
    unchanged" here is a statement about the guard rather than about a command
    that could never have done anything.
    """
    rel, args, tree = _ENV_VECTORS[var]
    victim = _victim(tmp_path / "victim")
    fixture = _make_repo(tmp_path / "allowed" / "fix", tree=tree)
    before = _victim_state(victim)

    env = _env(allowed=str(tmp_path / "allowed"), denied=str(victim))
    env["HOME"] = str(tmp_path / "home")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    env[var] = str(victim / rel) if rel else str(victim)

    got = _git(*args, cwd=fixture, env=env)

    assert got.returncode == nogit.BLOCK_EXIT, (
        f"{var} armed at the victim was NOT refused (rc={got.returncode}). "
        f"stderr={got.stderr!r}")
    assert nogit.BANNER in got.stderr
    after = _victim_state(victim)
    assert after == before, (
        f"{var}: the guard returned {got.returncode} but the victim CHANGED.\n"
        f"  changed: "
        f"{sorted(k for k in set(after[2]) | set(before[2]) if after[2].get(k) != before[2].get(k))[:6]}")


@pytest.mark.parametrize("args", [
    ("commit", "--allow-empty", "-m", "ok"),
    ("checkout", "-f", "HEAD"),
    ("add", "-A"),
    ("branch", "-m", "main", "trunk"),
])
def test_a_fixture_driving_its_OWN_repo_through_git_env_is_allowed(
        args, tmp_path, no_real_git_writes):
    """🔴 The control that separates a working guard from a useless one.

    "Refuse whenever a git env var is set" passes every negative test above
    while breaking every legitimate fixture — and a red-only demonstration
    rewards exactly that design. The policy therefore bounds where the
    variables POINT, never whether they are present.
    """
    own = _make_repo(tmp_path / "allowed" / "own")
    env = _env(allowed=str(tmp_path / "allowed"),
               denied=str(tmp_path / "nowhere"))
    env["GIT_DIR"] = str(own / ".git")
    env["GIT_WORK_TREE"] = str(own)
    got = _git(*args, cwd=own, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"a fixture driving its OWN repo via GIT_DIR/GIT_WORK_TREE was "
        f"refused: {got.stderr!r}")


def test_dev_null_as_a_config_sink_is_not_a_violation(
        tmp_path, no_real_git_writes):
    """`GIT_CONFIG_GLOBAL=/dev/null` is the standard hermetic-harness idiom.

    MEASURED: it was **590 of the 593** firings on the first armed run of the
    full suite. Refusing it makes the policy permanently red across
    `scripts/tests`, and a permanently-red gate gets switched off.
    """
    fixture = _make_repo(tmp_path / "allowed" / "fix")
    env = _env(allowed=str(tmp_path / "allowed"),
               denied=str(tmp_path / "nowhere"))
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    got = _git("commit", "--allow-empty", "-m", "ok", cwd=fixture, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"/dev/null as a config sink was refused: {got.stderr!r}")


def test_the_policy_survives_a_child_with_a_REPLACING_env(
        tmp_path, no_real_git_writes):
    """🔴 The roots are BAKED INTO the shim; the environment only widens them.

    MEASURED: three tests in this suite build `env={HOME, GIT_CONFIG_GLOBAL,
    GIT_CONFIG_SYSTEM, PATH}` for a hermetic `git init` in their own tmp_path.
    `PATH` still reaches the shim, so the shim runs — and when the policy lived
    only in the environment it ran with NO roots and refused them.
    """
    stub = Path(os.environ[nogit.STUB_DIR_ENV])
    fixture_parent = tmp_path / "fix"
    fixture_parent.mkdir()
    victim = _victim(tmp_path / "victim")
    before = _victim_state(victim)

    bare = {"HOME": str(tmp_path), "PATH": os.environ["PATH"],
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    assert nogit.ROOTS_ENV not in bare and nogit.DENY_ROOTS_ENV not in bare
    assert (stub / "git").exists()

    # The allow floor still permits a fixture under the session's basetemp…
    ok = subprocess.run(["git", "init", "-q", str(fixture_parent / "r")],
                        cwd=str(tmp_path), env=bare, capture_output=True,
                        text=True, timeout=TIMEOUT)
    assert ok.returncode != nogit.BLOCK_EXIT, (
        f"a hermetic `git init` under tmp_path was refused by a child with a "
        f"replacing env: {ok.stderr!r}")

    # …and the deny floor still refuses the tree under test, with no
    # DEVRC_TEST_GIT_DENIED_ROOTS present at all.
    bad = subprocess.run(
        ["git", "-C", str(nogit.repo_root()), "config", "nogit.probe", "1"],
        cwd=str(tmp_path), env=bare, capture_output=True, text=True,
        timeout=TIMEOUT)
    assert bad.returncode == nogit.BLOCK_EXIT, (
        "with the policy variables absent, a write to the tree under test was "
        f"ALLOWED (rc={bad.returncode}) — the deny floor is not baked in")
    # 🔴 Compared against the state captured BEFORE the run. The previous
    # version of this line read `_victim_state(v) == _victim_state(v)`, a
    # tautology that is true of any two identical calls and could never fail.
    assert _victim_state(victim) == before


def test_the_exec_path_farm_survives_a_child_with_a_REPLACING_env(
        tmp_path, no_real_git_writes):
    """The farm must not depend on the plugin's exported GIT_EXEC_PATH.

    A child built with a replacing `env=` keeps only `PATH` — so the shim
    re-exports `GIT_EXEC_PATH` itself on every call. Without that, such a child
    reaches the shim, the shim execs the real git, and the real git restores
    its own libexec for every grandchild.
    """
    root = tmp_path / "w"
    root.mkdir()
    victim = _victim(root / "victim")
    allowed = root / "allowed"
    allowed.mkdir()
    fixture = _make_repo(allowed / "fix")
    subprocess.run(
        [_real(), "-C", str(fixture), "config", "alias.x",
         f"!git -C {victim} commit --allow-empty -m REPLACED"],
        check=True, capture_output=True, timeout=TIMEOUT)

    bare = {"HOME": str(root / "home"), "PATH": os.environ["PATH"],
            nogit.ROOTS_ENV: str(allowed),
            nogit.DENY_ROOTS_ENV: str(victim)}
    Path(bare["HOME"]).mkdir(parents=True, exist_ok=True)
    assert "GIT_EXEC_PATH" not in bare

    before = _victim_state(victim)
    subprocess.run(["git", "-C", str(fixture), "x"], cwd=str(root), env=bare,
                   capture_output=True, text=True, timeout=TIMEOUT)
    assert _victim_state(victim) == before, (
        "with GIT_EXEC_PATH absent from a replacing env, an alias child "
        "reached the real git and wrote the victim")


def test_the_deny_floor_is_a_union_not_an_override(tmp_path, no_real_git_writes):
    """A test may widen what it can write to; nothing may un-deny the repo."""
    body = nogit.git_body("/bin/true", Path("/dev/null"),
                          allowed_roots="/BAKED_ALLOW",
                          denied_roots="/BAKED_DENY", stub_dir=tmp_path)
    assert f'roots="${{{nogit.ROOTS_ENV}:-/BAKED_ALLOW}}"' in body
    assert (f'denied="/BAKED_DENY${{{nogit.DENY_ROOTS_ENV}:+'
            f':${{{nogit.DENY_ROOTS_ENV}}}}}"') in body, (
        "the deny floor is not a union — a caller supplying "
        f"{nogit.DENY_ROOTS_ENV} could drop the tree under test from it")


def test_the_deny_floor_is_a_union_in_practice(tmp_path, no_real_git_writes):
    """The LIVE half of the test above — a string in a body is not behaviour."""
    stub = tmp_path / "stub"
    victim = _victim(tmp_path / "victim")
    _install(stub, allowed=str(tmp_path), denied=str(victim))
    env = dict(os.environ)
    env["PATH"] = str(stub) + os.pathsep + _real_path()
    # A caller supplying its OWN deny list must not drop the baked one.
    env[nogit.DENY_ROOTS_ENV] = str(tmp_path / "somewhere-else")
    env.pop(nogit.ROOTS_ENV, None)
    before = _victim_state(victim)
    with _l2_off():
        got = subprocess.run([str(stub / "git"), "-C", str(victim), "commit",
                              "--allow-empty", "-m", "UNDENY"],
                             cwd=str(tmp_path), env=env, capture_output=True,
                             text=True, timeout=TIMEOUT)
    assert got.returncode == nogit.BLOCK_EXIT, (
        "a caller-supplied deny list replaced the baked one instead of adding "
        f"to it (rc={got.returncode})")
    assert _victim_state(victim) == before


def test_the_session_scrubs_an_inherited_git_env(no_real_git_writes):
    """A pytest session has no business inheriting any of these."""
    for var in (*nogit.GIT_LOCATION_ENV, *nogit.GIT_EXEC_ENV,
                nogit.MODE_ENV, "GIT_CONFIG_COUNT"):
        assert var not in os.environ, (
            f"{var} survived into the test session: an ambient value redirects "
            "or executes for every `git` call in the suite")


def test_the_prescan_fixture_chain_cannot_reach_a_victim(
        tmp_path, no_real_git_writes):
    """🔴 REGRESSION TEST FOR THE ACTUAL INCIDENT.

    `scripts/repo-cos/tests/test_prescan.py::_init_clone` is the identified
    culprit, and its steps are replayed verbatim below:

        _git(clone, "config", "user.email", "t@t")   -> the `t <t@t>` author
        _git(clone, "commit", "-m", "seed")          -> `trunk@{1}: commit: seed`
        _git(clone, "branch", "-M", branch)          -> `renamed main to trunk`
        _git(clone, "push", "origin", f"HEAD:{branch}") -> the push storm

    Every one of those is `git -C <tmp clone> …` — absolute, fixture-scoped,
    and cleared by two audits. With an ambient `GIT_DIR` they all land in
    whatever repo it names.
    """
    victim = _victim(tmp_path / "victim")
    before = _victim_state(victim)
    clone = _make_repo(tmp_path / "allowed" / "clone")

    env = _env(allowed=str(tmp_path / "allowed"), denied=str(victim))
    env["GIT_DIR"] = str(victim / ".git")

    steps = (("config", "user.email", "t@t"),
             ("config", "user.name", "t"),
             ("commit", "--allow-empty", "--quiet", "-m", "seed"),
             ("branch", "-M", "trunk"),
             ("remote", "set-head", "origin", "trunk"),
             ("push", "--quiet", "origin", "HEAD:trunk"))
    refused = 0
    for args in steps:
        got = _git(*args, cwd=clone, env=env)
        assert got.returncode == nogit.BLOCK_EXIT, (
            f"`git {' '.join(args)}` reached the victim (rc={got.returncode})")
        refused += 1

    # 🔴 Compared against the LENGTH OF THE LIST, not against the literal 6.
    # `assert refused == 6` pins the fixture's own length: adding a step and
    # forgetting to bump the number is the only way it can fail, and dropping a
    # step plus the number keeps it green while covering less.
    assert refused == len(steps)
    after = _victim_state(victim)
    assert after == before, (
        "the victim changed despite every step being refused: "
        f"{sorted(k for k in set(after[2]) | set(before[2]) if after[2].get(k) != before[2].get(k))[:6]}")


# --------------------------------------------------------------------------- #
# THE MUTATION — attribute the block to the GUARD, not to the command
# --------------------------------------------------------------------------- #
def test_the_same_write_succeeds_with_the_shim_removed(
        tmp_path, no_real_git_writes):
    """🔴 Disable the policy and watch the offending command pass silently."""
    victim = _make_repo(tmp_path / "victim")
    subprocess.run([_real(), "remote", "add", "origin", "/tmp/original.git"],
                   cwd=str(victim), check=True, capture_output=True,
                   timeout=TIMEOUT)

    argv = ["git", "remote", "set-url", "origin", "/tmp/poisoned.git"]

    guarded = subprocess.run(
        argv, cwd=str(victim),
        env=_env(allowed=str(tmp_path / "nowhere"), denied=str(victim)),
        capture_output=True, text=True, timeout=TIMEOUT)
    assert guarded.returncode == nogit.BLOCK_EXIT

    with _l2_off():
        unguarded = subprocess.run(
            argv, cwd=str(victim),
            env=_env(allowed=str(tmp_path / "nowhere"), denied=str(victim),
                     path=_real_path()),
            capture_output=True, text=True, timeout=TIMEOUT)
    assert unguarded.returncode == 0, (
        f"the mutation control did not pass: {unguarded.stderr!r}. The "
        "'blocked' results above cannot be attributed to the guard.")

    url = subprocess.run([_real(), "remote", "get-url", "origin"],
                         cwd=str(victim), capture_output=True, text=True,
                         timeout=TIMEOUT)
    assert url.stdout.strip() == "/tmp/poisoned.git", (
        "with the guard removed the write did not land, so the negative "
        "controls prove nothing about the guard")


# --------------------------------------------------------------------------- #
# 🔴 THE HANDSHAKE — a filename is not a guard
# --------------------------------------------------------------------------- #
def test_the_handshake_accepts_this_policys_own_shim(tmp_path):
    stub = tmp_path / "stub"
    _install(stub, allowed=str(tmp_path), denied=str(nogit.repo_root()))
    answer = _handshake(stub)
    assert answer is not None
    assert answer["fingerprint"] == nogit.policy_fingerprint()
    assert answer["roots"] == str(tmp_path)


@pytest.mark.parametrize("flavour", ["passthrough", "silent", "wrong-token",
                                     "not-executable", "absent"])
def test_the_handshake_rejects_anything_that_is_not_this_policys_shim(
        flavour, tmp_path):
    """🔴 THE MEASURED FAILURE THIS REPLACES.

    `_inherited_stub_dir()` used to adopt any directory containing a FILE
    NAMED `git` and then skip `install()` entirely. Measured with
    `DEVRC_TEST_GIT_STUB_DIR` pointed at a plain passthrough script: a write to
    the repo-under-test landed **rc 0, no banner**, the session marker was
    written anyway, and pytest reported "1 passed". The docstring claimed the
    opposite. A filename is walkable by anything called `git`.
    """
    d = tmp_path / "foreign"
    d.mkdir()
    exe = d / "git"
    if flavour == "passthrough":
        write_exec(exe, f'exec {_real()} "$@"\n')
    elif flavour == "silent":
        write_exec(exe, "exit 0\n")
    elif flavour == "wrong-token":
        write_exec(
            exe,
            f'printf "{nogit.HANDSHAKE_MAGIC} deadbeefdeadbeef\\n"\n'
            'printf "roots=/\\n"\nprintf "denied=/\\n"\nexit 0\n')
    elif flavour == "not-executable":
        write_exec(exe, "exit 0\n")
        exe.chmod(0o644)
    else:
        pass  # absent: the directory has no `git` at all

    assert _handshake(d) is None, (
        f"a {flavour} `git` answered the handshake — the check is not "
        "verifying that this is THIS policy's shim")


def test_a_foreign_inherited_shim_FAILS_the_session_instead_of_being_adopted(
        tmp_path, monkeypatch):
    """🔴 And it must FAIL, not fall back to self-installing.

    Falling back would be safe for this session and would silently hide a
    misconfigured outer runner — which is the state that produced the measured
    "1 passed" with no guard at all.
    """
    d = tmp_path / "foreign"
    d.mkdir()
    exe = d / "git"
    write_exec(exe, f'exec {_real()} "$@"\n')
    monkeypatch.setenv(nogit.STUB_DIR_ENV, str(d))
    with _l2_off(), pytest.raises(nogit_plugin.ForeignGitShim) as exc:
        nogit_plugin._inherited_stub_dir()
    assert str(d) in str(exc.value)


@pytest.mark.parametrize("flaw", ["wrong-deny-floor", "audit-mode"])
def test_an_inherited_shim_scoped_to_another_tree_is_REPLACED_not_adopted(
        flaw, tmp_path, monkeypatch):
    """Genuinely ours, but not usable as-is: self-install instead of adopting.

    A shim whose baked deny floor does not carry THIS tree is structurally
    inert here — in the nix sandbox the source sits inside an allowed root, so
    without the deny entry every write this policy exists to stop is permitted.
    Same for a shim baked in audit mode.

    🔴 IT RETURNS None (self-install) RATHER THAN RAISING, and that direction
    was MEASURED. Raising cost nine red tests: several tests in this suite copy
    the repo to a tmp dir and run a NESTED pytest in it. The nested session
    inherits `DEVRC_TEST_GIT_STUB_DIR`, its `repo_root()` is the copy, and the
    outer shim's deny roots name the original — a healthy arrangement, not a
    misconfiguration. The FOREIGN case still raises, because there the question
    is not "which tree" but "is this a guard at all".
    """
    stub = tmp_path / "stub"
    if flaw == "wrong-deny-floor":
        _install(stub, allowed=str(tmp_path), denied=str(tmp_path / "nope"))
    else:
        _install(stub, allowed=str(tmp_path), denied=str(nogit.repo_root()),
                 mode=nogit.MODE_AUDIT)
    assert _handshake(stub) is not None, "the shim IS this policy's"
    monkeypatch.setenv(nogit.STUB_DIR_ENV, str(stub))
    with _l2_off():
        assert nogit_plugin._inherited_stub_dir() is None, (
            f"a shim with {flaw} was ADOPTED. It answers the handshake, so it "
            "is ours — but adopting it runs this session under a policy that "
            "does not bound this tree.")


def test_the_fingerprint_changes_when_the_policy_changes(monkeypatch):
    """The handshake token must track the policy's LOGIC, not just its name.

    Otherwise a shim generated by an older revision — with a ledger entry this
    revision removed, or a check it did not have — would be adopted as current.
    """
    before = nogit.policy_fingerprint()
    monkeypatch.setattr(nogit, "GIT_LOCATION_ENV",
                        (*nogit.GIT_LOCATION_ENV, "GIT_MADE_UP_FOR_THE_TEST"))
    after = nogit.policy_fingerprint()
    assert after != before, (
        "changing the environment ledger did not change the policy "
        "fingerprint, so a shim built from a DIFFERENT policy would answer the "
        "handshake as if it were this one")


# --------------------------------------------------------------------------- #
# 🔴 L2 — the in-process layer that was completely inert
# --------------------------------------------------------------------------- #
def test_both_launch_policies_survive_in_the_popen_chain(no_real_git_writes):
    """🔴 MEASURED INERT: `Popen.__mro__` was `['_NoLaunchPopen','Popen',…]`.

    Both plugins captured the PRISTINE `subprocess.Popen` at IMPORT time and
    each assigned its own subclass OF THAT PRISTINE CLASS, so whichever session
    fixture ran last discarded the other's patch outright — and each teardown
    restored the pristine class, discarding the survivor too. This policy's L2
    was the loser, and an absolute-path `git config` against a denied repo
    returned 0 with no banner. Zero tests referenced it, so nothing was red.

    Both now subclass whatever `Popen` IS at patch time, which composes in
    either order. This pins that both are still in the chain.
    """
    names = [c.__name__ for c in subprocess.Popen.__mro__]
    assert "_NoGitPopen" in names, (
        f"this policy's L2 is not in the Popen chain: {names}. An absolute-path "
        "`git` launched by the pytest process reaches the real binary "
        "unchecked.")
    assert "_NoLaunchPopen" in names, (
        f"the launcher policy's L2 is not in the Popen chain: {names}. One of "
        "the two patches has deleted the other — the exact failure this "
        "capture-at-patch-time shape exists to prevent.")


def test_an_absolute_path_git_write_is_intercepted_in_process(
        tmp_path, no_real_git_writes):
    """L2's whole job, exercised. It had NO test coverage at all.

    PATH cannot shadow an absolute path, so this is the only layer that can see
    it — and `_redirect_argv` had zero references in the previous 868-line
    battery.
    """
    victim = _victim(tmp_path / "victim")
    before = _victim_state(victim)
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = subprocess.run([_real(), "-C", str(victim), "config",
                          "nogit.absprobe", "1"], cwd=str(tmp_path), env=env,
                         capture_output=True, text=True, timeout=TIMEOUT)
    assert got.returncode == nogit.BLOCK_EXIT, (
        f"an ABSOLUTE-path git write to a denied repo was not intercepted "
        f"(rc={got.returncode}) — L2 is inert")
    assert _victim_state(victim) == before


@pytest.mark.parametrize("argv,expect_redirect", [
    (["/usr/bin/git", "status"], True),
    (["git", "status"], False),            # bare name: L1's job
    (["/usr/bin/gitk", "status"], False),  # basename is not exactly `git`
    ("git status", False),                 # a shell string goes via /bin/sh
    ([], False),
])
def test_the_absolute_path_redirect_is_narrow(argv, expect_redirect, tmp_path):
    """`_redirect_argv` must not perturb launches that are not its business."""
    got = nogit_plugin._redirect_argv(argv, tmp_path)
    assert (got is not None) == expect_redirect, (
        f"_redirect_argv({argv!r}) -> {got!r}")
    if expect_redirect:
        assert got[0] == str(tmp_path / "git")
        assert got[1:] == list(argv)[1:]


def test_the_shim_dir_and_the_farm_are_not_redirected(tmp_path):
    """A git that is ALREADY the shim must not be rewritten to itself.

    Rewriting the farm's `git` in particular would send every git-spawned child
    through an extra process for no gain.
    """
    stub = tmp_path / "stub"
    _install(stub, allowed=str(tmp_path), denied=str(tmp_path / "nope"))
    assert nogit_plugin._redirect_argv([str(stub / "git"), "status"],
                                       stub) is None
    assert nogit_plugin._redirect_argv(
        [str(nogit.exec_path_dir(stub) / "git"), "status"], stub) is None


# --------------------------------------------------------------------------- #
# AUTOUSE — protection a test must ask for is protection the next test forgets
# --------------------------------------------------------------------------- #
def test_the_popen_patch_composes_in_EITHER_order(tmp_path):
    """🔴 ORDER-INDEPENDENT, because the session's order is not controllable.

    `test_both_launch_policies_survive_in_the_popen_chain` observes whatever
    order this session's two autouse fixtures happened to run in — and a
    mutation sweep proved that is not enough: restoring the import-time capture
    in this module SURVIVED, because with the launcher policy patching second
    it subclasses ours and the chain looks fine either way.

    This applies both patches explicitly, in both orders, and requires both
    classes to survive each time. Only capture-at-patch-time satisfies it.
    """
    from testlib import nolaunch_plugin  # noqa: PLC0415

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    saved = subprocess.Popen
    try:
        for label, first, second in (
                ("nolaunch then nogit",
                 nolaunch_plugin._patch_subprocess,
                 nogit_plugin._patch_subprocess),
                ("nogit then nolaunch",
                 nogit_plugin._patch_subprocess,
                 nolaunch_plugin._patch_subprocess)):
            subprocess.Popen = saved
            first(a)
            second(b)
            names = [c.__name__ for c in subprocess.Popen.__mro__]
            assert "_NoGitPopen" in names, (
                f"{label}: this policy's L2 was deleted by the other patch. "
                f"chain={names}")
            assert "_NoLaunchPopen" in names, (
                f"{label}: the launcher policy's L2 was deleted by ours. "
                f"chain={names}")
    finally:
        subprocess.Popen = saved


def test_a_git_inside_the_sessions_tmp_tree_is_NOT_redirected(tmp_path):
    """🔴 A test's own `git` DOUBLE must reach the test, not the guard.

    MEASURED when L2 started working: `test_ship_converge.py::_git_shim` builds
    a `git` under `tmp_path` that is real for every subcommand except one
    deliberately sabotaged verb. L2 rewrote the call to the shim, the sabotage
    never happened, and two tests validating `ship.sh`'s currency guard failed
    on assertions about the guard they were validating.

    Both directions are asserted, because the exclusion is only correct if the
    thing it exists to catch is still caught.
    """
    stub = tmp_path / "stub"
    _install(stub, allowed=str(tmp_path), denied=str(tmp_path / "nope"))

    double = tmp_path / "double"
    double.mkdir()
    write_exec(double / "git", "exit 0\n")
    assert nogit_plugin._redirect_argv(
        [str(double / "git"), "status"], stub, tmp_path) is None, (
        "a `git` a test built inside its own tmp tree was rewritten to the "
        "shim — L2 replaced the test's instrument with the guard")

    # …and the real binary, outside the tmp tree, still IS redirected.
    assert nogit_plugin._redirect_argv(
        [_real(), "status"], stub, tmp_path) is not None, (
        "an absolute path to the REAL git was not redirected — the exclusion "
        "has swallowed the only thing L2 exists to catch")




def test_a_deep_init_with_no_dotdot_is_still_allowed(
        tmp_path, no_real_git_writes):
    """The positive half of the unresolvable-path fix.

    `git init a/b/c` CREATES its intermediate directories, so "neither the path
    nor its parent resolves" is the NORMAL case for it — refusing every such
    path would make `init` unusable. Only `..` can walk out of an allowed root
    lexically, so only `..` gets the sentinel.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    env = _env(allowed=str(allowed), denied=str(tmp_path / "nowhere"))
    got = _git("init", "-q", str(allowed / "a" / "b" / "c"), cwd=allowed,
               env=env)
    assert got.returncode == 0, (
        f"a deep `git init` inside an allowed root was refused: "
        f"{got.stderr!r}")
    assert (allowed / "a" / "b" / "c" / ".git").is_dir()


def test_an_unresolvable_dotdot_inside_an_allowed_root_is_refused_anyway(
        tmp_path, no_real_git_writes):
    """🔴 THE DELIBERATE COST OF THE FIX ABOVE, PINNED SO IT IS NOT A SURPRISE.

    `<allowed>/exists-later/../ok` resolves to `<allowed>/ok`, which is IN
    bounds — but `exists-later` does not exist, so the path cannot be
    canonicalised and the shim cannot tell it from the victim-bound twin above.
    It is refused.

    That is a false positive, and it is the right trade: the shape is one
    nobody writes by hand, the refusal is loud and names this policy, and the
    alternative is resolving `..` lexically — which is wrong in the presence of
    symlinks and would hand the escape back. Pinned so that a future reader
    meets it here rather than in a confusing red test.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    env = _env(allowed=str(allowed), denied=str(tmp_path / "nowhere"))
    got = _git("init", "-q", str(allowed / "exists-later" / ".." / "ok"),
               cwd=allowed, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        "an unresolvable path containing `..` was ALLOWED. It happens to land "
        "in bounds here, but the shim cannot know that — and the identical "
        "shape aimed at the victim is a measured bypass.")
    assert nogit.BANNER in got.stderr



def test_protected_without_asking():
    """Deliberately does NOT request the fixture."""
    resolved = shutil.which("git")
    assert resolved is not None
    assert Path(resolved).parent == Path(os.environ[nogit.STUB_DIR_ENV]), (
        "a test that never requested the fixture is unprotected — "
        "`autouse=True` has been lost from the session fixture")


def test_the_session_marker_was_written_and_says_how(no_real_git_writes):
    """A guard that never loaded and a guard that found nothing look alike.

    🔴 AND THE MARKER MUST SAY WHICH. It used to be written identically whether
    this session installed the shim or ADOPTED an inherited one — so a run that
    adopted a foreign `git`, installed nothing, and enforced nothing still
    produced a healthy-looking marker. `via=` is what separates those.
    """
    lines = nogit.recorded(no_real_git_writes)
    markers = [ln for ln in lines
               if ln.startswith(nogit_plugin.SESSION_MARKER)]
    assert markers, (
        "no session marker in the shim log — this plugin did not load, and a "
        "clean log would be indistinguishable from a clean run")
    assert any(f"via={nogit_plugin.VIA_SELF}" in ln
               or f"via={nogit_plugin.VIA_INHERITED}" in ln
               for ln in markers), (
        f"the session marker carries no `via=` field: {markers[:2]!r}. A "
        "self-installed and an inherited shim would be indistinguishable in "
        "the log a runner reads.")
