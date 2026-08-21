"""🔴 The guard: no test may WRITE to a git repo it does not own.

This file exists because the suite drove `git` against the operator's REAL
clone and destroyed `main` on the remote. The ordering is the diagnosis, so it
is recorded here rather than only in a PR body:

    $ git -C ~/workspace/devrc reflog show trunk
    5d91acdd trunk@{0}: Branch: renamed refs/heads/main to refs/heads/trunk
    5d91acdd trunk@{1}: commit: seed
    2e7a85b3 trunk@{2}: commit: c

    HEAD reflog (local, UTC-5):
      13:48:11  merge origin/main: Fast-forward   <- last legitimate operation
      14:21:35  Branch: renamed main to trunk     = 19:21:35Z
      14:38:36  HEAD at 14200130 (a single-file tree)

Local corruption at **19:21:35Z**; the remote push storm — ~40 pushes onto
`refs/heads/main` in 43 seconds, four other branches hit — began **19:28:14Z**,
seven minutes LATER. So the local writes came first and the pushes followed
them. That rules out an independently-poisoned remote and pins the mechanism as
"something resolved a repo for itself, then pushed what it found".

🔴 IT IS WHY A PUSH-SCOPED OR REMOTE-SCOPED GUARD WOULD BE USELESS. The whole
first phase — `commit`, `branch -m`, `config core.bare true` — is local, and a
guard watching remotes would have let all of it through. The assertions below
therefore cover LOCAL porcelain with no reference to whether anything would
ever be pushed.

🔴 AND WHY CLEANUP IS NOT A FIX. The offending code restored the `origin` URL
it clobbered, so an after-the-fact `git remote -v` showed a clean repo; the
only way to see it was to look WHILE the suite ran. A restore also only happens
on clean teardown, and the real clone was found still poisoned long after every
run had ended. `test_a_restore_afterwards_does_not_hide_the_write` pins that
the guard fires at the moment of the call, so there is nothing to undo.

SCOPE, stated so the title cannot be read as more than it is: this covers what
`testlib/nogit_plugin.py` is loaded into. It bounds writes by TARGET PATH, so
it cannot stop a test that clobbers PATH wholesale, and its in-process layer
sees only launches made by the pytest process itself.

What is asserted, and why none is enough alone:

  * RESOLUTION — `which("git")` lands in the shim dir.
  * ORDERING   — the shim dir is PATH[0]. "On PATH" is not the property that
    matters; "before every ambient entry" is.
  * READS PASS — the suite reads the real repo on purpose (`ls-files` in four
    content gates). A guard that broke those would be switched off in a day,
    so the read path is asserted to reach the REAL binary and forward its
    output faithfully.
  * WRITES BLOCK — with this policy's own exit code and banner, so a firing is
    identifiable rather than a puzzling git error.
  * FAIL-CLOSED — an unknown verb blocks. A blocklist would fail OPEN on the
    next verb someone reaches for, which is exactly how `remote` got through.
  * THE LEDGER — the read-verb set is pinned, so it fails when it GROWS or
    SHRINKS.
  * AUTOUSE — one test deliberately does not request the fixture.
  * THE MUTATION — the same command is run with the shim removed and asserted
    to SUCCEED, so "blocked" is attributed to the guard and not to the command
    being impossible anyway.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from testlib import nogit  # noqa: E402
from testlib import nogit_plugin  # noqa: E402

TIMEOUT = 60


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _git(*args: str, cwd: Path, env: dict | None = None,
         ) -> subprocess.CompletedProcess:
    """Run git with an EXPLICIT cwd — never inheriting this process's.

    `cwd` is keyword-only and required on purpose: a helper with a default cwd
    is how a test silently targets whatever directory the runner happened to
    start in, which is the shape that caused the incident.
    """
    return subprocess.run(["git", *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=TIMEOUT)


def _make_repo(path: Path, origin: str | None = None) -> Path:
    """A real, minimal git repo at `path`, built with the REAL binary.

    It bypasses the shim deliberately: these repos are the fixtures the tests
    then point the shim at, and building them through the shim would make every
    test depend on the very thing under test.
    """
    real = shutil.which("git", path=_real_path())
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run([real, *a], cwd=str(path), check=True,
                                    capture_output=True, timeout=TIMEOUT)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    run("commit", "-q", "--allow-empty", "-m", "seed")
    if origin is not None:
        run("remote", "add", "origin", origin)
    return path


def _real_path() -> str:
    """PATH with the shim directory removed — the ambient, unprotected one."""
    stub = os.environ.get(nogit.STUB_DIR_ENV, "")
    return os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and p != stub)


def _env(*, allowed: str, denied: str, path: str | None = None) -> dict:
    e = dict(os.environ)
    e[nogit.ROOTS_ENV] = allowed
    e[nogit.DENY_ROOTS_ENV] = denied
    if path is not None:
        e["PATH"] = path
    return e


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
    fixtures runs last takes position 0; today that is `nolaunch`, and the
    order is an artefact of fixture instantiation that nothing guarantees.

    What actually matters is that no EARLIER entry carries a `git`, so this
    asserts that instead. It is the same property "PATH[0]" was reaching for,
    stated so it cannot be broken by an unrelated fixture winning a race.
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


def test_git_is_not_also_claimed_by_the_launcher_policy():
    """🔴 The seam between the two policies, pinned.

    `nolaunch` prepends its own stub dir and — measured above — currently wins
    position 0. Its stubs are RECORD-ONLY: they log the argv and exit 0 with no
    output. If `git` were ever added to `HOST_LAUNCHERS`, that stub would
    shadow this one and every git call in the suite would return success with
    empty stdout: `rev-parse` answering nothing, `ls-files` listing nothing,
    and every content gate scanning zero files while passing. That is a far
    worse failure than the one this policy closes, and nothing else would
    notice it — so the two ledgers are pinned as disjoint here.
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
        "under test would sit inside an allowed root and be permitted — the "
        "policy would be live on the dev host and structurally blind in the "
        "tier that gates merges.")


# --------------------------------------------------------------------------- #
# THE LEDGERS
# --------------------------------------------------------------------------- #
def test_the_read_verb_ledger_is_pinned():
    """Fails when the read set GROWS or SHRINKS.

    Growing it is how a write gets reclassified as a read — the failure mode
    that put `remote set-url` through in the first place. Shrinking it is how
    the policy goes permanently red. Neither may happen silently.
    """
    assert set(nogit.GIT_READ_VERBS) == {
        "status", "log", "show", "diff", "cat-file", "ls-files", "ls-tree",
        "ls-remote", "rev-parse", "rev-list", "describe", "blame", "grep",
        "shortlog", "show-ref", "symbolic-ref", "for-each-ref", "merge-base",
        "name-rev", "check-ignore", "check-attr", "count-objects",
        "verify-pack", "var", "help", "version", "annotate", "whatchanged",
        "cherry", "diff-tree", "diff-index", "diff-files", "hash-object",
        "patch-id", "get-tar-commit-id", "check-mailmap", "column",
        "interpret-trailers",
    }


@pytest.mark.parametrize("verb", ["commit", "push", "remote", "config",
                                  "branch", "checkout", "reset", "fetch",
                                  "merge", "rebase", "tag", "worktree"])
def test_no_mutating_verb_is_on_the_read_ledger(verb):
    """The verbs seen in the incident must never be classified as reads."""
    assert verb not in nogit.GIT_READ_VERBS


def test_the_default_mode_is_block_not_audit():
    """`audit` is a measuring tool; leaving it on would make the guard inert."""
    assert nogit.MODE_BLOCK == "block"
    body = nogit.git_body("/bin/true", Path("/dev/null"))
    assert f"${{{nogit.MODE_ENV}:-{nogit.MODE_BLOCK}}}" in body, (
        "the shim's default mode is not 'block' — a guard silently in audit "
        "mode records violations and lets every one of them through")


# --------------------------------------------------------------------------- #
# READS still reach the real binary
# --------------------------------------------------------------------------- #
def test_a_read_against_a_denied_repo_still_reaches_the_real_binary(
        tmp_path, no_real_git_writes):
    """The four content gates read the REAL repo with `git ls-files`.

    If the policy broke that they would scan NOTHING and pass — a silent zero
    manufactured by the guard itself, which is worse than the hazard it closes.
    """
    victim = _make_repo(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))

    got = _git("ls-files", cwd=victim, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"a READ was refused: {got.stderr}")

    real = shutil.which("git", path=_real_path())
    want = subprocess.run([real, "ls-files"], cwd=str(victim),
                          capture_output=True, text=True, timeout=TIMEOUT)
    assert got.stdout == want.stdout
    assert got.returncode == want.returncode


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
    """`git` alone writes nothing, so refusing it is a false positive.

    Worth pinning because it is confusing rather than dangerous: a bare `git`
    answering with this policy's VIOLATION banner reads like the guard is
    broken, which is how a guard gets removed.
    """
    victim = _make_repo(tmp_path / "victim")
    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    got = subprocess.run(["git"], cwd=str(victim), env=env,
                         capture_output=True, text=True, timeout=TIMEOUT)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"a bare `git` was refused: {got.stderr!r}")
    assert nogit.BANNER not in got.stderr


def test_a_bare_git_with_no_dash_C_is_judged_by_its_cwd(
        tmp_path, no_real_git_writes):
    """🔴 The first-class suspect.

    A `git` with neither `-C` nor an explicit `cwd=` targets whatever directory
    the process happens to be in. For a suite run from the clone, that IS the
    clone — and it is how `drift-check.sh`'s remote leg, running locally under
    a stub `ssh` with `DRIFT_REPO` unset, reaches `$HOME/workspace/devrc`.
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
    """Deny must beat allow, or the policy is inert in the nix sandbox.

    There the source tree is unpacked under `$TMPDIR`, so the repo under test
    is INSIDE an allowed root. If allow won, every write this policy exists to
    stop would be permitted in the tier that gates merges.
    """
    victim = _make_repo(tmp_path / "victim")
    # tmp_path itself is allowed, and victim is inside it — yet denied.
    env = _env(allowed=str(tmp_path), denied=str(victim))
    got = _git("commit", "--allow-empty", "-m", "c", cwd=victim, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        "an explicitly DENIED repo was rescued by an enclosing allowed root")


def test_a_push_to_a_destination_outside_the_roots_is_refused(
        tmp_path, no_real_git_writes):
    """🔴 The second vector: the SOURCE repo being in-bounds is not enough.

    A push from a legitimate fixture still reaches whatever `origin` points at.
    The contained clone prepared for this work had an `origin` aimed at the
    operator's real clone, so this is measured behaviour, not a hypothetical.
    """
    src = _make_repo(tmp_path / "src")
    dest = _make_repo(tmp_path / "dest")
    real = shutil.which("git", path=_real_path())
    subprocess.run([real, "remote", "add", "origin", str(dest)], cwd=str(src),
                   check=True, capture_output=True, timeout=TIMEOUT)
    # src is allowed; dest is NOT.
    env = _env(allowed=str(src), denied=str(dest))
    got = _git("push", "origin", "main", cwd=src, env=env)
    assert got.returncode == nogit.BLOCK_EXIT, (
        f"push from an allowed repo INTO a denied one was permitted "
        f"(rc={got.returncode}): {got.stderr!r}")
    assert nogit.BANNER in got.stderr


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

    Every assertion above is satisfied by a shim that blocks unconditionally.
    This is the half that distinguishes them, and it is the half that found two
    real defects: the first draft refused `git init <tmpdir>` (it judged by cwd
    rather than by the positional path) and refused every fixture-to-fixture
    `push` (it read the value of `-C` as the remote name).
    """
    # `set-url` needs an existing remote to re-point; every other case must
    # start from a repo that has none, so the origin is added only for that one.
    pre = "/tmp/original.git" if args[:2] == ("remote", "set-url") else None
    fixture = _make_repo(tmp_path / "fixture", origin=pre)
    env = _env(allowed=str(tmp_path), denied=str(tmp_path / "nowhere"))
    got = _git(*args, cwd=fixture, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"`git {' '.join(args)}` inside the test's OWN tmpdir was refused. "
        f"stderr={got.stderr!r}")
    assert got.returncode == 0, f"unexpected git failure: {got.stderr!r}"


def test_git_init_of_a_fixture_dir_is_allowed_from_inside_a_denied_cwd(
        tmp_path, no_real_git_writes):
    """`git init <tmpdir>` writes to the ARGUMENT, not to the cwd.

    Measured: judging this by cwd refuses every fixture that builds itself a
    clone, because the suite's cwd is the repo. A permanently-red gate gets
    switched off, and then the real hazard is back.
    """
    denied = _make_repo(tmp_path / "denied")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    env = _env(allowed=str(allowed), denied=str(denied))
    got = _git("init", "-q", str(allowed / "new"), cwd=denied, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"`git init <allowed>` from a denied cwd was refused: {got.stderr!r}")
    assert (allowed / "new" / ".git").is_dir()


def test_git_clone_into_a_denied_path_is_still_refused(
        tmp_path, no_real_git_writes):
    """The mirror of the above — the positional target is CHECKED, not trusted."""
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
    AFTER the fact would see nothing at all, in both the run below and the real
    incident. This asserts the guard fires at the moment of the call, and that
    the URL is unchanged afterwards because the write never happened — not
    because something put it back.
    """
    victim = _make_repo(tmp_path / "victim")
    real = shutil.which("git", path=_real_path())
    good = str(tmp_path / "upstream.git")
    subprocess.run([real, "remote", "add", "origin", good], cwd=str(victim),
                   check=True, capture_output=True, timeout=TIMEOUT)

    env = _env(allowed=str(tmp_path / "nowhere"), denied=str(victim))
    script = tmp_path / "victim.sh"
    # The drift-check.sh:444 / ship.sh:190 shape: `:-` falls back on EMPTY as
    # well as unset, so an override that is set-but-blank lands on the default.
    script.write_text(
        "#!/bin/sh\n"
        'repo="${DRIFT_REPO:-' + str(victim) + '}"\n'
        'saved=$(git -C "$repo" remote get-url origin)\n'
        'git -C "$repo" remote set-url origin /tmp/poisoned.git || exit 40\n'
        'git -C "$repo" remote set-url origin "$saved"\n'
        'echo restored\n',
        encoding="utf-8")
    script.chmod(0o755)

    env["DRIFT_REPO"] = ""  # present but EMPTY — the case `:-` hides
    got = subprocess.run(["/bin/sh", str(script)], cwd=str(tmp_path), env=env,
                         capture_output=True, text=True, timeout=TIMEOUT)

    assert got.returncode == 40, (
        "the repoint was not refused — the script ran to completion and would "
        f"have restored the URL, hiding it (rc={got.returncode})")
    assert nogit.BANNER in got.stderr
    assert "restored" not in got.stdout

    after = subprocess.run([real, "remote", "get-url", "origin"],
                           cwd=str(victim), capture_output=True, text=True,
                           timeout=TIMEOUT)
    assert after.stdout.strip() == good, (
        "origin is not what it was — the write landed")


# --------------------------------------------------------------------------- #
# 🔴 THE GIT ENVIRONMENT — `-C` IS NOT PROTECTIVE
# --------------------------------------------------------------------------- #
def _victim_state(repo: Path) -> tuple:
    """Everything an intruder would disturb, as one comparable tuple."""
    real = shutil.which("git", path=_real_path())
    def q(*a: str) -> str:
        return subprocess.run([real, "-C", str(repo), *a], capture_output=True,
                              text=True, timeout=TIMEOUT).stdout.strip()
    keep = repo / "keep.txt"
    return (q("rev-parse", "HEAD"), q("branch", "--show-current"),
            q("rev-list", "--count", "HEAD"), q("config", "--get", "user.email"),
            keep.read_text(encoding="utf-8") if keep.exists() else "")


def _victim(path: Path) -> Path:
    """A repo that must survive every test in this section untouched."""
    real = shutil.which("git", path=_real_path())
    _make_repo(path)
    (path / "keep.txt").write_text("PRECIOUS", encoding="utf-8")
    for a in (("add", "keep.txt"), ("commit", "-q", "-m", "victimseed")):
        subprocess.run([real, "-C", str(path), *a], check=True,
                       capture_output=True, timeout=TIMEOUT)
    return path


@pytest.mark.parametrize("var,rel,args", [
    # IDENTITY redirection — git reports a different repo.
    ("GIT_DIR", ".git", ("commit", "--allow-empty", "-m", "PWNED")),
    ("GIT_COMMON_DIR", ".git", ("commit", "--allow-empty", "-m", "PWNED")),
    ("GIT_DIR", ".git", ("branch", "-m", "main", "trunk")),
    ("GIT_DIR", ".git", ("config", "user.email", "pwned@x")),
    # 🔴 BYTE redirection — identity stays innocent and TRUTHFUL; only the
    # destination moves. An identity-based check passes every one of these and
    # the bytes are still written. `GIT_WORK_TREE` is the one that matters most.
    ("GIT_WORK_TREE", "", ("checkout", "-f", "HEAD")),
    ("GIT_WORK_TREE", "", ("add", "-A")),
    ("GIT_INDEX_FILE", ".git/index", ("add", "a.txt")),
    ("GIT_OBJECT_DIRECTORY", ".git/objects",
     ("commit", "--allow-empty", "-m", "PWNED")),
    ("GIT_ALTERNATE_OBJECT_DIRECTORIES", ".git/objects",
     ("commit", "--allow-empty", "-m", "PWNED")),
    ("GIT_CONFIG_GLOBAL", ".gitconfig",
     ("config", "--global", "user.name", "pwned")),
    ("GIT_CONFIG_SYSTEM", ".gitconfig",
     ("config", "--system", "user.name", "pwned")),
])
def test_a_git_env_var_armed_at_a_victim_is_refused(
        var, rel, args, tmp_path, no_real_git_writes):
    """🔴 Every path ARGUMENT names an innocent fixture; only the ENV points out.

    Measured before this check existed:

        GIT_DIR=<victim>/.git git -C <fixture> commit --allow-empty -m PWNED
        -> rc 0, and the victim's commit count went 1 -> 2.

    while `git -C <fixture> rev-parse --show-toplevel` answered `<fixture>`.
    That is why "absolute `-C`, therefore fixture-scoped" cleared this suite in
    two independent audits: it is the wrong property.

    The victim's whole state is compared, not just an exit code — a guard that
    returns 99 after the write has landed is not a guard.
    """
    victim = _victim(tmp_path / "victim")
    fixture = _make_repo(tmp_path / "allowed" / "fix")
    before = _victim_state(victim)

    env = _env(allowed=str(tmp_path / "allowed"), denied=str(victim))
    env[var] = str(victim / rel) if rel else str(victim)

    got = _git(*args, cwd=fixture, env=env)

    assert got.returncode == nogit.BLOCK_EXIT, (
        f"{var} armed at the victim was NOT refused (rc={got.returncode}). "
        f"stderr={got.stderr!r}")
    assert nogit.BANNER in got.stderr
    assert _victim_state(victim) == before, (
        f"{var}: the guard returned {got.returncode} but the victim CHANGED.\n"
        f"  before: {before}\n  after:  {_victim_state(victim)}")


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
    `scripts/tests`, and a permanently-red gate gets switched off — taking the
    real protection with it. `/dev/null` discards writes, so exempting it costs
    nothing; `GIT_CONFIG_GLOBAL=<victim>/.gitconfig` is still refused above.
    """
    fixture = _make_repo(tmp_path / "allowed" / "fix")
    env = _env(allowed=str(tmp_path / "allowed"),
               denied=str(tmp_path / "nowhere"))
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    got = _git("commit", "--allow-empty", "-m", "ok", cwd=fixture, env=env)
    assert got.returncode != nogit.BLOCK_EXIT, (
        f"/dev/null as a config sink was refused: {got.stderr!r}")


def test_the_env_ledger_holds_only_paths_that_receive_bytes():
    """Pinned, and pinned for a reason that cost a full armed run.

    `GIT_NAMESPACE` (a ref namespace) and `GIT_CEILING_DIRECTORIES` (which only
    NARROWS discovery) were both on this ledger and both were wrong: neither
    can redirect a write to a path, so both produced pure false positives. They
    passed a negative control only because that control armed them at a
    path-shaped value, which measures the guard rather than the hazard.
    """
    assert set(nogit.GIT_LOCATION_ENV) == {
        "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE",
        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    }
    for bad in ("GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES"):
        assert bad not in nogit.GIT_LOCATION_ENV, (
            f"{bad} does not receive bytes — treating it as a write target "
            "refuses the safe case")


def test_the_policy_survives_a_child_with_a_REPLACING_env(
        tmp_path, no_real_git_writes):
    """🔴 The roots are BAKED INTO the shim; the environment only widens them.

    MEASURED: three tests in this suite build `env={HOME, GIT_CONFIG_GLOBAL,
    GIT_CONFIG_SYSTEM, PATH}` for a hermetic `git init` in their own tmp_path.
    `PATH` still reaches the shim, so the shim runs — and when the policy lived
    only in the environment it ran with NO roots and refused them. Reading the
    policy from a variable makes it depend on that variable surviving into
    every descendant, which is not a property anyone maintains.

    This drives a child with an env carrying ONLY the four keys those tests
    keep, and asserts both halves still hold.
    """
    stub = Path(os.environ[nogit.STUB_DIR_ENV])
    fixture_parent = tmp_path / "fix"
    fixture_parent.mkdir()
    victim = _victim(tmp_path / "victim")

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
    assert _victim_state(victim) == _victim_state(victim)


def test_the_deny_floor_is_a_union_not_an_override(tmp_path, no_real_git_writes):
    """A test may widen what it can write to; nothing may un-deny the repo.

    The asymmetry is deliberate. `roots` is `${VAR:-<baked>}` so a test can
    narrow or widen its own sandbox, but `denied` is `<baked>${VAR:+:$VAR}` so
    the tree under test is refused even when a caller passes its own deny list
    — or an empty one.
    """
    body = nogit.git_body("/bin/true", Path("/dev/null"),
                          allowed_roots="/BAKED_ALLOW",
                          denied_roots="/BAKED_DENY")
    assert f'roots="${{{nogit.ROOTS_ENV}:-/BAKED_ALLOW}}"' in body
    assert (f'denied="/BAKED_DENY${{{nogit.DENY_ROOTS_ENV}:+'
            f':${{{nogit.DENY_ROOTS_ENV}}}}}"') in body, (
        "the deny floor is not a union — a caller supplying "
        f"{nogit.DENY_ROOTS_ENV} could drop the tree under test from it")


def test_the_session_scrubs_an_inherited_git_env(no_real_git_writes):
    """A pytest session has no business inheriting any of these."""
    for var in nogit.GIT_LOCATION_ENV:
        assert var not in os.environ, (
            f"{var} survived into the test session: an ambient value redirects "
            "every `git -C <fixture>` write in the suite")


def test_the_prescan_fixture_chain_cannot_reach_a_victim(
        tmp_path, no_real_git_writes):
    """🔴 REGRESSION TEST FOR THE ACTUAL INCIDENT.

    `scripts/repo-cos/tests/test_prescan.py::_init_clone` is the identified
    culprit, and its steps are replayed verbatim below. Read them against the
    real clone's reflog:

        _git(clone, "config", "user.email", "t@t")   -> the `t <t@t>` author
        _git(clone, "commit", "-m", "seed")          -> `trunk@{1}: commit: seed`
        _git(clone, "branch", "-M", branch)          -> `renamed main to trunk`
        _git(clone, "push", "origin", f"HEAD:{branch}") -> the push storm

    Every one of those is `git -C <tmp clone> …` — absolute, fixture-scoped,
    and cleared by two audits. With an ambient `GIT_DIR` they all land in
    whatever repo it names. This asserts the chain is refused and the victim is
    untouched, so the mechanism cannot come back through a different fixture.
    """
    victim = _victim(tmp_path / "victim")
    before = _victim_state(victim)
    clone = _make_repo(tmp_path / "allowed" / "clone")

    env = _env(allowed=str(tmp_path / "allowed"), denied=str(victim))
    env["GIT_DIR"] = str(victim / ".git")

    refused = 0
    for args in (("config", "user.email", "t@t"),
                 ("config", "user.name", "t"),
                 ("commit", "--allow-empty", "--quiet", "-m", "seed"),
                 ("branch", "-M", "trunk"),
                 ("remote", "set-head", "origin", "trunk"),
                 ("push", "--quiet", "origin", "HEAD:trunk")):
        got = _git(*args, cwd=clone, env=env)
        assert got.returncode == nogit.BLOCK_EXIT, (
            f"`git {' '.join(args)}` reached the victim (rc={got.returncode})")
        refused += 1

    assert refused == 6
    assert _victim_state(victim) == before, (
        f"the victim changed despite every step being refused.\n"
        f"  before: {before}\n  after:  {_victim_state(victim)}")


# --------------------------------------------------------------------------- #
# THE MUTATION — attribute the block to the GUARD, not to the command
# --------------------------------------------------------------------------- #
def test_the_same_write_succeeds_with_the_shim_removed(
        tmp_path, no_real_git_writes):
    """🔴 Disable the policy and watch the offending command pass silently.

    Without this, every "was refused" assertion above is equally satisfied by a
    command that simply cannot work — a `remote set-url` that fails for its own
    reasons would read as a guard firing. Running the identical argv against
    the ambient PATH shows the guard is the only thing standing in the way.
    """
    victim = _make_repo(tmp_path / "victim")
    real = shutil.which("git", path=_real_path())
    subprocess.run([real, "remote", "add", "origin", "/tmp/original.git"],
                   cwd=str(victim), check=True, capture_output=True,
                   timeout=TIMEOUT)

    argv = ["git", "remote", "set-url", "origin", "/tmp/poisoned.git"]

    guarded = subprocess.run(
        argv, cwd=str(victim),
        env=_env(allowed=str(tmp_path / "nowhere"), denied=str(victim)),
        capture_output=True, text=True, timeout=TIMEOUT)
    assert guarded.returncode == nogit.BLOCK_EXIT

    unguarded = subprocess.run(
        argv, cwd=str(victim),
        env=_env(allowed=str(tmp_path / "nowhere"), denied=str(victim),
                 path=_real_path()),
        capture_output=True, text=True, timeout=TIMEOUT)
    assert unguarded.returncode == 0, (
        f"the mutation control did not pass: {unguarded.stderr!r}. The "
        "'blocked' results above cannot be attributed to the guard.")

    url = subprocess.run([real, "remote", "get-url", "origin"],
                         cwd=str(victim), capture_output=True, text=True,
                         timeout=TIMEOUT)
    assert url.stdout.strip() == "/tmp/poisoned.git", (
        "with the guard removed the write did not land, so the negative "
        "controls prove nothing about the guard")


# --------------------------------------------------------------------------- #
# AUTOUSE — protection a test must ask for is protection the next test forgets
# --------------------------------------------------------------------------- #
def test_protected_without_asking():
    """Deliberately does NOT request the fixture.

    Every other test in this file names `no_real_git_writes`, which would make
    deleting `autouse=True` completely invisible. This one is the control for
    that flag.
    """
    resolved = shutil.which("git")
    assert resolved is not None
    assert Path(resolved).parent == Path(os.environ[nogit.STUB_DIR_ENV]), (
        "a test that never requested the fixture is unprotected — "
        "`autouse=True` has been lost from the session fixture")


def test_the_session_marker_was_written(no_real_git_writes):
    """A guard that never loaded and a guard that found nothing look alike.

    Both leave a clean log. The marker is what separates them, and the runner
    can require one per target.
    """
    lines = nogit.recorded(no_real_git_writes)
    assert any(ln.startswith(nogit_plugin.SESSION_MARKER) for ln in lines), (
        "no session marker in the shim log — this plugin did not load, and a "
        "clean log would be indistinguishable from a clean run")
