"""`scripts/claim-work.sh` — the claim-by-push lock on a shared next-step queue.

WHAT IS UNDER TEST, AND WHAT DELIBERATELY IS NOT
------------------------------------------------
The absence of duplicate work is UNOBSERVABLE — you cannot write a test that
asserts two sessions did not build the same thing. So this file exercises the
MECHANISM instead, and the mechanism is two claims:

    (1) the claim NAMESPACE IS GLOBAL — one canonical remote resolved from the
        script's own location, never from the caller's cwd;
    (2) publishing an ORPHAN commit to `refs/heads/claim/<slug>` on that remote
        is refused for a second claimant by the receiving git, not by a check we
        take and then act on.

🔴 (1) IS NOT COSMETIC AND IT WAS BROKEN. Until 2026-08-26 the remote came from
`$PWD`, so the namespace was PER-ORIGIN: the same canonical slug claimed from
`devrc` and from `homelab-talos` both returned rc 0 CLAIMED, one ref on each
origin, no warning. The queue this locks is global — handoff docs live in devrc
while the work happens elsewhere, which is literally the shape of the measured
incident — so a per-origin namespace made the whole mechanism inert cross-repo.
`test_the_claim_namespace_is_global_not_per_origin` is the regression cover, and
`test_an_unresolvable_canonical_remote_degrades_rather_than_using_the_cwd` pins
the other half: NO cwd fallback, because a fallback reinstates the bug silently.

Every case below either exercises those claims or attacks them:

  * 🔴 THE REFUSAL HAS TWO DIFFERENT MECHANISMS AND ONLY ONE IS THE ATOMICITY.
    Measured 2026-08-26, with the exact strings git emits:
      - `test_a_true_concurrent_create_is_refused_by_the_ref_transaction_cas`
        pins the server-side compare-and-swap: both clients send `old=0000…`,
        the second create fails `cannot lock ref '…': reference already exists`.
        THIS is what covers the first mover, and the orphan root plays no part.
      - `test_a_serialized_loser_is_refused_client_side_and_cannot_fast_forward`
        pins the other one: the second session's scratch repo does not hold the
        winner's object, so git refuses BEFORE sending — `(fetch first)`.
    The old single test asserted `"non-fast-forward" or "fetch first"` and called
    itself a test of the CAS. It was green only because it exercised the
    serialized path; the CAS message contains neither string, so it would have
    gone red in the very case its name claimed.

  * 🔴 `test_defeating_the_lock_with_force_lets_the_second_session_win` is the
    MUTATION CONTROL, and it is the reason the collision assertions are worth
    anything. `claude/RULES.md`: *"A test you have not watched FAIL proves
    nothing"* — and *"the red half must be a mutation, not the script's
    absence"*. It rewrites the ONE push that is the lock to carry `--force`, and
    asserts the second session then WINS. Without it, `rc == 10` could be
    produced by any unrelated failure.

  * The fail-open cases matter as much as the lock. This tool runs at the start
    of every resumed session, so a bug in it is felt by every `/resume`. It must
    never block one: no origin / not a repo / unreachable remote ⇒ exit 0 with a
    loud stderr warning. `--strict` (rc 20) exists so a test can tell "degraded"
    apart from "won", which an exit-0-either-way contract otherwise hides.

HERMETIC BY CONSTRUCTION
------------------------
Every fixture is a BARE repo in `tmp_path` used as `origin` over a plain
filesystem path. No network, no GitHub, no `.git` in the source tree — which is
the class that passes on the dev host and fails in the nix sandbox, where the
check builds from a `cp -r` store copy with no repository at all. The only thing
this module reads out of the source tree is the script FILE.

Git's environment comes from `testlib.hermetic_git`, so background maintenance
cannot write a `.git/objects/maintenance.lock` into a fixture mid-fingerprint.

⚠ ONE EXCEPTION, ON PURPOSE: `hermetic_git` pins `GIT_CONFIG_GLOBAL=/dev/null`,
and that is EXACTLY the surface that carries the operator's `core.hooksPath`. A
suite that always neutralises it is structurally blind to a global `pre-push`
hook blocking a claim push — measured 2026-08-26, and a blocking hook makes the
lock silently inert (push fails ⇒ degrade ⇒ exit 0 ⇒ "unclaimed"). So
`test_a_global_pre_push_hook_cannot_make_the_lock_inert` builds its environment
WITHOUT that pin and with a real `$HOME/.gitconfig`.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib import hermetic_git, mockbin  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "claim-work.sh"

# The rc vocabulary the script documents at the top of itself. Spelled here as
# literals ON PURPOSE: `claude/RULES.md` — "never derive a test's expectation
# from the implementation it tests".
RC_OK = 0
RC_USAGE = 2
RC_TAKEN = 10
RC_TAKEN_STALE = 11
RC_MINE = 12
RC_DEGRADED_STRICT = 20

CLAIM_NS = "refs/heads/claim/"

# 🔴 THE LOCK, AS A LITERAL. This exact fragment is the one push in the script
# that must never force. The mutation control below rewrites it, and
# `test_the_lock_line_is_present_exactly_once` pins that it is still findable —
# because a mutation control that silently matches nothing reports a clean sweep
# while testing the unmutated script.
LOCK_PUSH = 'push -q --no-verify origin "${sha}:${CLAIM_NS}${SLUG}" 2>"$push_err"'
MUTANT_PUSH = 'push -q --no-verify --force origin "${sha}:${CLAIM_NS}${SLUG}" 2>"$push_err"'

# 🔴 A SAFETY NET, NOT A FIXTURE. The script's DEFAULT remote is now the origin
# of the repo containing the script — and in this suite the script lives in the
# real devrc checkout, whose origin is GitHub. A test that forgot `--repo`/
# `--remote` would therefore push a claim ref to a REAL remote. This sentinel is
# below both flags in the resolution order, so every test that passes one still
# overrides it, and one that passes neither degrades against a path that cannot
# exist instead of reaching the network.
UNREACHABLE_SENTINEL = "/nonexistent/claim-work-tests-must-pass-repo-or-remote.git"


# ── fixtures ──────────────────────────────────────────────────────────────────

def _env(**overrides: str) -> dict:
    """Hermetic git env. `GIT_CONFIG_GLOBAL=/dev/null` is part of it, so the
    identity a claim carries can only come from the fixture repo's LOCAL config —
    which is exactly the path the script uses in production."""
    base = {"DEVRC_CLAIM_REMOTE": UNREACHABLE_SENTINEL}
    base.update(overrides)
    return hermetic_git.hermetic_git_env(**base)


def _git(*args: str, cwd: Path | None = None, env: dict | None = None,
         check: bool = True, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        env=env or _env(),
        input=input,
        capture_output=True, text=True, check=check,
    )


def _bare_origin(root: Path, name: str = "origin.git") -> Path:
    path = root / name
    _git("init", "-q", "--bare", str(path))
    return path


def _session(root: Path, origin: Path, who: str, name: str = "work") -> Path:
    """A working clone standing in for one agent session, with its OWN identity
    so a collision report can be checked to name the RIGHT claimer."""
    path = root / name
    _git("init", "-q", "-b", "main", str(path))
    _git("-C", str(path), "remote", "add", "origin", str(origin))
    _git("-C", str(path), "config", "user.name", who)
    _git("-C", str(path), "config", "user.email",
         f"{who.lower().replace(' ', '.')}@localhost")
    return path


def _cwd_env(origin: Path, **overrides: str) -> dict:
    """🔴 THE ENV FOR A TEST THAT MUST NOT PASS `--repo`.

    `_run(repo=…)` ALWAYS passes `--repo <path>`, and `--repo` makes the owner
    token derive from a REPO PATH — which is cwd-invariant by construction. So
    every ownership test written that way was structurally blind to the cwd half
    of the token, and BOTH directions of the 2026-08-26 ownership bug lived
    exactly there. `/resume` step 6 says in as many words: run the bare command
    from wherever you are, do NOT pass `--repo`.

    This gives the production shape instead — no flag, the remote from the
    environment (resolution order 3, which `--repo`/`--remote` would outrank) —
    so the token comes from `$PWD` the way it does in a real session.
    """
    return _env(DEVRC_CLAIM_REMOTE=str(origin), **overrides)


def _worktree(session: Path, path: Path, branch: str = "wt") -> Path:
    """A linked worktree of `session`, so ownership can be measured across the
    isolation this repo MANDATES for agent work. `worktree add` needs a commit,
    and `_session` leaves an unborn HEAD.

    IDEMPOTENT on the `base` commit, because the round-4 fan-out test needs FIVE
    worktrees of one clone and `checkout -b base` can only succeed once. Without
    this the second call dies on "a branch named 'base' already exists" and the
    test that exists to measure the fan-out cannot build one.
    """
    if not _git("-C", str(session), "rev-parse", "--verify", "-q", "refs/heads/base",
                check=False).stdout.strip():
        _git("-C", str(session), "checkout", "-q", "-b", "base")
        _git("-C", str(session), "commit", "-q", "--allow-empty", "-m", "base")
    _git("-C", str(session), "worktree", "add", "-q", str(path), "-b", branch)
    return path


def _machine_id(root: Path, name: str, value: str) -> Path:
    """A stand-in `/etc/machine-id`. The script reads the FILE named by
    `DEVRC_CLAIM_MACHINE_ID_FILE`, never a value from the environment, so two
    files is a genuine simulation of two machines rather than a value backdoor —
    and it is the only way to measure the cross-HOST half on one host."""
    p = root / name
    p.write_text(value + "\n", encoding="utf-8")
    return p


def _run(*args: str, repo: Path | None = None, script: Path | None = None,
         env: dict | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke the script through `bash <path>`, never through its shebang.

    `/usr/bin/env` does not exist in the nix build sandbox and this repo has a
    repo-wide guard about it; `bash <path>` sidesteps the interpreter lookup
    entirely, which is also how `test_ship_converge.py` drives `ship.sh`.

    `cwd` matters now: the whole point of the 2026-08-26 fix is that the cwd must
    NOT decide the claim namespace, and a test cannot show that without varying it.
    """
    argv = ["bash", str(script or SCRIPT)]
    if repo is not None:
        argv += ["--repo", str(repo)]
    argv += list(args)
    return subprocess.run(argv, capture_output=True, text=True, env=env or _env(),
                          cwd=str(cwd) if cwd else None)


def _install_script(root: Path, origin: Path, name: str = "canon") -> Path:
    """A COPY of the script inside its own git repo whose `origin` is `origin`.

    This is how the canonical-remote resolution is exercised hermetically: the
    script resolves its own realpath, walks to that repo's root and reads THAT
    repo's origin — so putting the copy in a fixture repo redirects the whole
    namespace without touching the real devrc remote.
    """
    repo = root / name
    _git("init", "-q", "-b", "main", str(repo))
    _git("-C", str(repo), "remote", "add", "origin", str(origin))
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    dest = repo / "scripts" / "claim-work.sh"
    shutil.copy2(SCRIPT, dest)
    return dest


def _remote_refs(origin: Path) -> dict:
    out = _git("-C", str(origin), "for-each-ref",
               "--format=%(refname)|%(objectname)").stdout
    return dict(
        (line.split("|", 1)[0], line.split("|", 1)[1])
        for line in out.splitlines() if "|" in line
    )


def _fingerprint(root: Path) -> dict:
    """Content hash of every file under `root`, `.git` INCLUDED and on purpose.

    A read that refreshes `.git/index` leaves contents identical and moves an
    mtime; hashing content (not mtimes) with `.git` in scope is what makes
    "this tool did not touch your repository" a checkable claim rather than a
    comfortable one. Git's auto-maintenance would otherwise drop a transient
    `maintenance.lock` in here — see `testlib/hermetic_git.py`.
    """
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            out[str(p.relative_to(root))] = ("link", os.readlink(p))
        elif p.is_file():
            out[str(p.relative_to(root))] = (
                "file", hashlib.sha256(p.read_bytes()).hexdigest())
    return out


# ── the happy path ────────────────────────────────────────────────────────────

def test_a_first_claim_wins_and_the_ref_lands_on_origin(tmp_path):
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")

    r = _run("topic-1", "--subject", "right-size the requests", repo=a)

    assert r.returncode == RC_OK, f"claim failed:\n{r.stdout}\n{r.stderr}"
    assert "CLAIMED" in r.stdout, r.stdout
    # A negative control on the fail-open path: a REACHABLE origin must not
    # degrade. Without this, every "exit 0" below could be a silent degradation.
    assert "DEGRADED" not in r.stderr, r.stderr

    refs = _remote_refs(origin)
    assert f"{CLAIM_NS}topic-1" in refs, refs


def test_the_claim_commit_is_an_orphan_root(tmp_path):
    """🔴 THIS IS WHY THE LOCK WORKS. A claim with a parent could fast-forward
    over someone else's claim; an unrelated root can never be a descendant of
    anything, so the second push is always a non-fast-forward."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    assert _run("topic-1", repo=a).returncode == RC_OK

    parents = _git("-C", str(origin), "log", "-1", "--format=%P",
                   f"{CLAIM_NS}topic-1").stdout.strip()
    assert parents == "", (
        f"the claim commit has parent(s) {parents!r} — it is not an orphan root, "
        f"so a second claim could fast-forward over the first and the lock is gone"
    )
    # The claim carries NO content — an empty tree. It is a marker, not a change,
    # so it can never conflict with anything and costs one 4-byte object.
    tree = _git("-C", str(origin), "log", "-1", "--format=%T",
                f"{CLAIM_NS}topic-1").stdout.strip()
    empty_tree = _git("-C", str(origin), "hash-object", "-t", "tree", "--stdin",
                      input="").stdout.strip()
    assert tree == empty_tree, (
        f"the claim commit's tree is {tree}, not git's empty tree {empty_tree} — "
        f"a claim must carry no content")


def test_a_second_session_claiming_the_same_slug_is_refused_and_told_who_holds_it(tmp_path):
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    b = _session(tmp_path, origin, "Session B", "b")

    first = _run("topic-4", "--subject", "right-size the requests", repo=a)
    assert first.returncode == RC_OK, first.stderr

    second = _run("topic-4", "--subject", "right-size the requests too", repo=b)
    assert second.returncode == RC_TAKEN, (
        f"the second session was not refused: rc={second.returncode}\n"
        f"{second.stdout}\n{second.stderr}"
    )
    assert "ALREADY CLAIMED" in second.stdout, second.stdout
    # WHO / WHEN / WHAT — a refusal that does not say who holds it just moves
    # the coordination problem somewhere the next session cannot see.
    assert "Session A" in second.stdout, f"the holder is not named:\n{second.stdout}"
    assert "right-size the requests" in second.stdout, (
        f"the holder's SUBJECT is not shown:\n{second.stdout}")
    assert re.search(r"when:\s+\d{4}-\d{2}-\d{2}T", second.stdout), (
        f"no ISO timestamp in the refusal:\n{second.stdout}")

    # And the winner still owns the ref — a refusal must not have half-written.
    assert _remote_refs(origin)[f"{CLAIM_NS}topic-4"] == _git(
        "-C", str(origin), "rev-parse", f"{CLAIM_NS}topic-4").stdout.strip()


# ── the atomicity claim, verified rather than asserted ────────────────────────

def test_a_true_concurrent_create_is_refused_by_the_ref_transaction_cas(tmp_path):
    """🔴 THE ATOMICITY, PINNED AS THE THING IT ACTUALLY IS.

    Two sessions that start at the same instant both see NO ref, so both send the
    update with `old = 0000…0` — a CREATE. The receiving git's ref transaction is
    a compare-and-swap on that expected value, so exactly one create can land.
    Reproduced deterministically with raw `update-ref`, which takes the expected
    old value explicitly and is therefore the same transaction a create-push
    performs, minus the transport.

    🔴 THE MESSAGE IS THE POINT. This rejection is `cannot lock ref '<ref>':
    reference already exists` — it contains NEITHER "non-fast-forward" NOR
    "fetch first", which is why the single old test that asserted those two
    strings while calling itself a test of the CAS would have gone red in exactly
    the case it claimed to cover. It was green because it exercised the OTHER
    mechanism (the test below).
    """
    origin = _bare_origin(tmp_path)
    work = _session(tmp_path, origin, "Session A", "w")
    env = _env()
    zero = "0" * 40

    def orphan(msg: str) -> str:
        tree = _git("-C", str(work), "mktree", input="", env=env).stdout.strip()
        return _git("-C", str(work), "commit-tree", tree,
                    input=msg, env=env).stdout.strip()

    one, two = orphan("claim one"), orphan("claim two")
    assert one != two, "the two claim commits must differ or this proves nothing"
    # Both objects must exist ON THE SERVER, or the refusal below could be
    # "nonexistent object" — a different mechanism wearing the same clothes.
    _git("-C", str(work), "push", "origin", f"{one}:refs/heads/staging/one")
    _git("-C", str(work), "push", "origin", f"{two}:refs/heads/staging/two")

    ref = f"{CLAIM_NS}race"
    first = _git("-C", str(origin), "update-ref", ref, one, zero, check=False)
    assert first.returncode == 0, f"the first create must succeed:\n{first.stderr}"

    second = _git("-C", str(origin), "update-ref", ref, two, zero, check=False)
    assert second.returncode != 0, (
        "git ACCEPTED a second create onto an existing ref — the whole mechanism "
        "rests on the ref transaction refusing it. Everything else in this file "
        "is downstream of this assertion."
    )
    blob = (second.stderr + second.stdout).lower()
    assert "reference already exists" in blob, (
        f"refused, but not by the compare-and-swap on the expected old value — "
        f"the refusal may be for an unrelated reason:\n{second.stderr}")
    assert _remote_refs(origin)[ref] == one, "the loser's commit overwrote the winner's"


def test_a_serialized_loser_is_refused_client_side_and_cannot_fast_forward(tmp_path):
    """The OTHER refusal — the one the script actually gets when the two sessions
    are not simultaneous, and the one the orphan root exists for.

    Modelled on the script's real shape: the loser pushes from a FRESH BARE repo
    that holds none of origin's objects. git then cannot prove a fast-forward and
    refuses before sending anything — `(fetch first)`. A repo that DID hold the
    winner's object would print `non-fast-forward` instead, which is where the
    unrelated-root property earns its keep; both are accepted here, and the CAS
    message is asserted ABSENT so this test cannot silently become a duplicate of
    the one above.
    """
    origin = _bare_origin(tmp_path)
    winner = _session(tmp_path, origin, "Session A", "w")
    env = _env()
    tree = _git("-C", str(winner), "mktree", input="", env=env).stdout.strip()
    one = _git("-C", str(winner), "commit-tree", tree, input="one", env=env).stdout.strip()
    ref = f"{CLAIM_NS}race"
    _git("-C", str(winner), "push", "origin", f"{one}:{ref}")

    loser = tmp_path / "loser.git"
    _git("init", "-q", "--bare", str(loser))
    _git("-C", str(loser), "remote", "add", "origin", str(origin))
    ltree = _git("-C", str(loser), "mktree", input="", env=env).stdout.strip()
    two = _git("-C", str(loser), "commit-tree", ltree, input="two", env=env).stdout.strip()

    bad = _git("-C", str(loser), "push", "origin", f"{two}:{ref}", check=False)
    assert bad.returncode != 0, "the serialized second push was ACCEPTED"
    blob = (bad.stderr + bad.stdout).lower()
    assert "fetch first" in blob or "non-fast-forward" in blob, (
        f"rejected, but not by the fast-forward check:\n{bad.stderr}")
    assert "reference already exists" not in blob, (
        "this path produced the ref-transaction CAS message, so it is no longer "
        "exercising the client-side fast-forward check it is named for")
    assert _remote_refs(origin)[ref] == one, "the loser's commit overwrote the winner's"


def test_six_concurrent_first_movers_resolve_to_exactly_one_winner(tmp_path):
    """🔴 THE FIRST-MOVER CASE, WHICH IS THE ONE A PRE-FLIGHT CHECK CANNOT COVER.

    Six sessions start at once; none can see any of the others, because at the
    moment each decides to start, none of the others has produced anything to
    see. A check-then-act design has a window here and this has none: the winner
    is decided by the server's ref transaction.

    🔴 THIS IS AN INVARIANT ASSERTION, NOT A MUTATION DETECTOR — do not count it
    as one. Measured against the `--force` mutant: it goes red only ~1 run in 3
    (pass/pass/fail over three runs; 15/15 green at HEAD). The reason is that
    concurrent pushes to one repo contend on git's own ref-transaction lock, so
    even with the non-fast-forward guarantee deleted most of the six still
    serialize and lose — for the WRONG reason, which is exactly the shape the
    mutation-sweep rule warns about. The deterministic kills for that mutant are
    `test_a_second_session_claiming_the_same_slug_is_refused_and_told_who_holds_it`
    and `test_defeating_the_lock_with_force_lets_the_second_session_win`, both
    serial by construction. Keep this test for the invariant it pins; do not
    quote it as coverage of the lock.
    """
    origin = _bare_origin(tmp_path)
    sessions = [_session(tmp_path, origin, f"Session {i}", f"s{i}") for i in range(6)]

    procs = [
        subprocess.Popen(
            ["bash", str(SCRIPT), "--repo", str(s), "hot-item",
             "--subject", f"from session {i}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env(),
        )
        for i, s in enumerate(sessions)
    ]
    results = [(p.wait(), *p.communicate()) for p in procs]
    codes = [rc for rc, _, _ in results]

    winners = [rc for rc in codes if rc == RC_OK]
    assert len(winners) == 1, (
        f"expected exactly ONE winner, got rc list {codes}\n"
        + "\n".join(f"--- rc={rc}\n{out}\n{err}" for rc, out, err in results)
    )
    assert all(rc == RC_TAKEN for rc in codes if rc != RC_OK), (
        f"a loser exited with something other than rc {RC_TAKEN}: {codes}")
    assert f"{CLAIM_NS}hot-item" in _remote_refs(origin)


# ── 🔴 the mutation control ───────────────────────────────────────────────────

def test_the_lock_line_is_present_exactly_once():
    """Guard the guard. If this fragment moves or is reworded, the mutation
    control below would sed nothing and pass against the UNMUTATED script — a
    clean report from a harness wired to nothing."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.count(LOCK_PUSH) == 1, (
        f"expected exactly one occurrence of the lock push\n  {LOCK_PUSH}\n"
        f"found {src.count(LOCK_PUSH)}. Update LOCK_PUSH/MUTANT_PUSH in this "
        f"file — until you do, the mutation control proves nothing."
    )
    assert "--force" not in src.split(LOCK_PUSH)[0].rsplit("\n", 1)[-1], (
        "the claim push line itself carries --force")


def test_defeating_the_lock_with_force_lets_the_second_session_win(tmp_path):
    """🔴 MUTATION CONTROL — the red half, and it is a MUTATION, not an absence.

    "The test fails on the pre-change tree because the file does not exist" is a
    vacuous red. So instead: keep everything, break exactly the one expression
    that can be wrong (the missing force flag on the claim push), and watch the
    collision disappear. If this test fails, the `rc == 10` assertions above are
    green for some reason other than the non-fast-forward rule.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    mutant = tmp_path / "claim-work-mutant.sh"
    mutated = src.replace(LOCK_PUSH, MUTANT_PUSH)
    assert mutated != src, "the mutation changed nothing — see LOCK_PUSH"
    mutant.write_text(mutated, encoding="utf-8")

    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    b = _session(tmp_path, origin, "Session B", "b")

    assert _run("topic-4", repo=a, script=mutant).returncode == RC_OK
    before = _remote_refs(origin)[f"{CLAIM_NS}topic-4"]

    second = _run("topic-4", repo=b, script=mutant)
    assert second.returncode == RC_OK, (
        f"the mutant did NOT defeat the lock (rc={second.returncode}). Either the "
        f"mutation missed, or the refusal in the tests above comes from something "
        f"other than the non-fast-forward rule — in which case those tests are "
        f"green for the wrong reason.\n{second.stdout}\n{second.stderr}"
    )
    after = _remote_refs(origin)[f"{CLAIM_NS}topic-4"]
    assert after != before, (
        "the mutant reported success but the ref did not move — the mutant "
        "exited 0 for some other reason and this control is not reaching the push"
    )


# ── 🔴 fails open ─────────────────────────────────────────────────────────────

def test_a_repo_with_no_origin_fails_open(tmp_path):
    plain = tmp_path / "no-origin"
    _git("init", "-q", "-b", "main", str(plain))
    r = _run("topic-1", repo=plain)
    assert r.returncode == RC_OK, (
        f"a repo with no origin must NOT block a resume: rc={r.returncode}\n{r.stderr}")
    assert "DEGRADED" in r.stderr, f"the degradation was silent:\n{r.stderr}"
    assert "no 'origin'" in r.stderr, r.stderr


def test_a_directory_that_is_not_a_git_repo_fails_open(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    r = _run("topic-1", repo=plain)
    assert r.returncode == RC_OK, r.stderr
    assert "DEGRADED" in r.stderr, r.stderr


def test_an_unreachable_origin_fails_open_and_strict_makes_it_visible(tmp_path):
    """Both halves matter. Exit 0 is the contract every `/resume` depends on;
    rc 20 under `--strict` is how anything automated can tell "I hold the claim"
    apart from "I could not find out", which exit-0-either-way hides."""
    missing = tmp_path / "gone.git"
    lenient = _run("topic-1", "--remote", str(missing))
    assert lenient.returncode == RC_OK, (
        f"an unreachable origin blocked the run: rc={lenient.returncode}\n{lenient.stderr}")
    assert "DEGRADED" in lenient.stderr, lenient.stderr

    strict = _run("--strict", "topic-1", "--remote", str(missing))
    assert strict.returncode == RC_DEGRADED_STRICT, (
        f"--strict must surface the degradation as rc {RC_DEGRADED_STRICT}, "
        f"got {strict.returncode}\n{strict.stderr}")


def test_a_degraded_check_does_not_report_the_slug_as_free(tmp_path):
    """🔴 The one way fail-open could become dangerous: collapsing "could not
    find out" into "nobody has it". An empty result cannot distinguish those two
    mechanisms, so the script must never print FREE when it could not ask."""
    r = _run("--check", "topic-1", "--remote", str(tmp_path / "gone.git"))
    assert r.returncode == RC_OK
    assert "FREE" not in r.stdout, (
        f"a check that could not reach origin reported the slug FREE:\n{r.stdout}")
    assert "DEGRADED" in r.stderr, r.stderr


# ── release / steal: a stale claim must not block an item forever ─────────────

def test_release_deletes_the_ref_and_the_slug_becomes_claimable_again(tmp_path):
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    b = _session(tmp_path, origin, "Session B", "b")

    assert _run("topic-4", repo=a).returncode == RC_OK
    assert _run("topic-4", repo=b).returncode == RC_TAKEN

    rel = _run("--release", "topic-4", repo=a)
    assert rel.returncode == RC_OK, rel.stderr
    assert f"{CLAIM_NS}topic-4" not in _remote_refs(origin), _remote_refs(origin)

    assert _run("topic-4", repo=b).returncode == RC_OK, (
        "the slug did not become claimable again after --release")


def test_releasing_a_slug_nobody_holds_is_a_no_op_not_an_error(tmp_path):
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    r = _run("--release", "never-claimed", repo=a)
    assert r.returncode == RC_OK, r.stderr
    assert "nothing to release" in r.stdout, r.stdout


def test_a_stale_claim_is_reported_separately_and_can_be_stolen(tmp_path):
    """A ref with no expiry would block an item forever, so the age is part of
    the verdict: rc 11 says "taken, but probably abandoned — decide"."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    b = _session(tmp_path, origin, "Session B", "b")

    old = int(time.time()) - 30 * 86400
    aged = _env(GIT_AUTHOR_DATE=f"{old} +0000")
    assert _run("topic-4", "--subject", "abandoned", repo=a, env=aged).returncode == RC_OK

    # The SAME ref, read at two ages: live claims are rc 10, this one is rc 11.
    # Measuring at one point only would not show the threshold does anything.
    check = _run("--check", "topic-4", repo=b)
    assert check.returncode == RC_TAKEN_STALE, (
        f"a 30-day-old claim was not reported STALE: rc={check.returncode}\n{check.stdout}")
    assert "STALE" in check.stdout, check.stdout

    fresh = _run("--check", "topic-4", repo=b,
                 env=_env(DEVRC_CLAIM_TTL_DAYS="60"))
    assert fresh.returncode == RC_TAKEN, (
        f"with a 60-day TTL the same claim must read LIVE, not stale — otherwise "
        f"the threshold is not what produced rc {RC_TAKEN_STALE}\n{fresh.stdout}")

    stolen = _run("--steal", "topic-4", "--subject", "taking over", repo=b)
    assert stolen.returncode == RC_OK, stolen.stderr
    # b now HOLDS it, so b asking about it is rc 12 (yours), not rc 10.
    assert _run("--check", "topic-4", repo=b).returncode == RC_MINE
    # …and a THIRD party still reads it as taken.
    c = _session(tmp_path, origin, "Session C", "c")
    assert _run("--check", "topic-4", repo=c).returncode == RC_TAKEN
    assert "taking over" in _run("--list", repo=b).stdout


@pytest.mark.parametrize("garbage", ["7d", "week"])
def test_a_non_numeric_ttl_falls_back_loudly_instead_of_reaching_the_arithmetic(
        tmp_path, garbage):
    """🔴 Measured on bash under this script's own `set -euo pipefail`, and the
    intuition is wrong in two directions:

        TTL=7d     -> `$(( TTL_DAYS * 86400 ))` ABORTS ("value too great for base")
        TTL=week   -> ABORTS ("week: unbound variable" — `set -u` catches the
                      bare-identifier case that would otherwise be a silent 0)
        TTL=""     -> silently 0, so every live claim reads STALE

    A tool whose entire contract is "never block a resume" must not die on a typo
    in an environment variable, so both shapes are parametrised here.

    The fixture is a claim a FEW SECONDS old on purpose: that is the only age at
    which "fell back to 7 days" and "became 0" give different answers. A 30-day
    fixture reads STALE either way and could not see the bug at all.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    b = _session(tmp_path, origin, "Session B", "b")

    aged = _env(GIT_AUTHOR_DATE=f"{int(time.time()) - 5} +0000")
    assert _run("topic-4", "--subject", "fresh", repo=a, env=aged).returncode == RC_OK

    r = _run("--check", "topic-4", repo=b, env=_env(DEVRC_CLAIM_TTL_DAYS=garbage))
    assert "not a whole number of days" in r.stderr, (
        f"TTL={garbage!r} was not rejected; stderr was:\n{r.stderr}")
    assert r.returncode == RC_TAKEN, (
        f"a 5-second-old claim read rc {r.returncode} with TTL={garbage!r}. It must "
        f"fall back to the DEFAULT — rc 1 means the arithmetic aborted the script, "
        f"rc {RC_TAKEN_STALE} means the threshold became 0.\n{r.stdout}\n{r.stderr}")
    assert "STALE" not in r.stdout, r.stdout

    # POSITIVE CONTROL: the same fixture DOES go stale at a zero threshold, so the
    # assertion above is discriminating rather than green for any fresh claim.
    zero = _run("--check", "topic-4", repo=b, env=_env(DEVRC_CLAIM_TTL_DAYS="0"))
    assert zero.returncode == RC_TAKEN_STALE, (
        f"TTL=0 did not make a 5-second-old claim stale (rc={zero.returncode}), so "
        f"the check above cannot tell a fallback from a zero\n{zero.stdout}")


# ── read-only paths ───────────────────────────────────────────────────────────

def test_check_is_read_only_and_creates_no_claim(tmp_path):
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")

    before = _remote_refs(origin)
    r = _run("--check", "topic-1", repo=a)
    assert r.returncode == RC_OK, r.stderr
    assert "FREE" in r.stdout, r.stdout
    assert _remote_refs(origin) == before, (
        "--check created a ref. A dry run that claims makes every dry run a collision.")

    # …and the claim that follows must still be winnable by the SAME session.
    assert _run("topic-1", repo=a).returncode == RC_OK


def test_the_claim_never_touches_the_callers_repository(tmp_path):
    """🔴 The script does all its work in a throwaway bare repo. It runs at the
    start of a resumed session, often in a shared checkout — a claim tool that
    can perturb the tree it is claiming work in would be worse than the
    collision it prevents."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    (a / "wip.txt").write_text("uncommitted work nobody else knows about\n")

    before = _fingerprint(a)
    assert _run("topic-1", "--subject", "x", repo=a).returncode == RC_OK
    # rc 12, not 10: `repo=a` is the session that claimed it.
    assert _run("--check", "topic-1", repo=a).returncode == RC_MINE
    assert _run("--list", repo=a).returncode == RC_OK
    after = _fingerprint(a)

    assert after == before, (
        "the caller's repository changed:\n"
        + "\n".join(
            f"  {k}: {before.get(k)} -> {after.get(k)}"
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k))
    )
    assert (a / "wip.txt").read_text() == "uncommitted work nobody else knows about\n"


def test_list_prints_every_live_claim_with_its_human_subject(tmp_path):
    """The SOFT signal. The exact-slug match is the hard lock; this list is how a
    session spots a semantic near-duplicate whose slug differs. It cannot catch
    those automatically and the script says so — but it can put them on screen."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")

    assert _run("topic-4", "--subject", "right-size the gitops requests", repo=a).returncode == RC_OK
    assert _run("topic-5", "--subject", "watch for a Preempted event", repo=a).returncode == RC_OK

    r = _run("--list", repo=a)
    assert r.returncode == RC_OK, r.stderr
    for expected in ("topic-4", "topic-5",
                     "right-size the gitops requests",
                     "watch for a Preempted event"):
        assert expected in r.stdout, f"{expected!r} missing from --list:\n{r.stdout}"
    assert "SOFT signal" in r.stdout, (
        f"--list must say the subject column is a soft signal, not a lock:\n{r.stdout}")


def test_list_on_an_empty_namespace_says_so_rather_than_failing(tmp_path):
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    r = _run("--list", repo=a)
    assert r.returncode == RC_OK, r.stderr
    assert "no live claims" in r.stdout, r.stdout


# ── slug determinism: the crux, and the documented weak point ─────────────────

@pytest.mark.parametrize("spelling", [
    "claudedocs/handoff-handoff-skill-hardening.md",
    "./claudedocs/handoff-handoff-skill-hardening.md",
    "/home/somebody/workspace/devrc/claudedocs/handoff-handoff-skill-hardening.md",
    "../devrc/claudedocs/handoff-handoff-skill-hardening.md",
])
def test_slug_for_is_the_same_from_every_spelling_of_the_same_doc(spelling):
    """🔴 IF TWO SESSIONS DERIVE DIFFERENT SLUGS, NOTHING IS LOCKED. That is why
    the derivation is CODE both runtimes call, not a convention in prose — and
    why an absolute path, a relative path and a `../` path must all collapse to
    the same answer."""
    r = _run("--slug-for", spelling, "1")
    assert r.returncode == RC_OK, r.stderr
    assert r.stdout.strip() == "handoff-skill-hardening-1", (
        f"{spelling} derived {r.stdout.strip()!r}")


def test_slug_for_distinguishes_the_rank(tmp_path):
    """The measured collision was next-step #1 vs #4 of ONE doc, so the rank has
    to be part of the identity or two different items share one lock."""
    one = _run("--slug-for", "claudedocs/handoff-x.md", "1").stdout.strip()
    four = _run("--slug-for", "claudedocs/handoff-x.md", "4").stdout.strip()
    assert one != four, f"rank collapsed: {one!r} == {four!r}"
    assert (one, four) == ("x-1", "x-4"), (one, four)


def test_slug_for_normalises_case_and_punctuation():
    r = _run("--slug-for", "claudedocs/Handoff-Some_Topic (v2).md", "2")
    assert r.returncode == RC_OK, r.stderr
    slug = r.stdout.strip()
    assert slug == "some-topic-v2-2", slug
    # And the result must be a legal slug for the claim path, or the derivation
    # would hand callers something the validator then rejects.
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*", slug), slug


# ── a typo must be LOUD, not failed open ─────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "BAD SLUG", "../escape", "-leading-dash", "has/slash", "trailing.",
    "with..dots",
    # 🔴 THE TWO THAT USED TO EXIT 0 DEGRADED, both measured 2026-08-26.
    # `grep -Eq` is LINE-based, so a slug whose FIRST line is legal matched and
    # sailed past validation into a refspec git would never accept.
    "good\nBAD SLUG",
    # …and this one matches the pattern and is ILLEGAL as a git ref, so the
    # pattern alone was never the whole contract.
    "foo.lock",
    "foo@{1}",
])
def test_a_malformed_slug_is_a_usage_error_and_claims_nothing(tmp_path, bad):
    """🔴 Fail-open is for "we could not find out", never for "the caller made a
    mistake". A typo'd slug quietly exiting 0 would claim nothing while the
    session believes it holds the item — the exact failure this tool removes."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    before = _remote_refs(origin)

    r = _run(bad, repo=a)
    assert r.returncode == RC_USAGE, (
        f"{bad!r} exited {r.returncode}, not the usage code {RC_USAGE}\n"
        f"{r.stdout}\n{r.stderr}")
    assert _remote_refs(origin) == before, f"{bad!r} still created a ref"


def test_the_two_slug_guards_are_deliberately_redundant():
    """🔴 A NOTE FOR THE NEXT MUTATION SWEEP, so a SURVIVOR is not misread.

    Slug validation has two independent guards — bash's whole-string `[[ =~ ]]`
    and `git check-ref-format` — and MEASURED 2026-08-26 they overlap almost
    completely: over all 157,120 strings of length ≤ 6 in `{a,b,0,1,9,._-}` there
    is not ONE that the pattern plus the `..`/trailing-`.`/`.lock` cases accept
    and `check-ref-format` rejects. `check-ref-format`'s only unique catch is the
    MULTI-LINE shape — which is exactly the bug that shipped.

    So mutating either guard alone SURVIVES the suite (the other one covers it),
    and mutating BOTH is killed by the `"good\nBAD SLUG"` case above. Measured:
      grep-line-based alone            -> SURVIVED
      check-ref-format removed alone   -> SURVIVED
      both                             -> KILLED, rc 0 instead of rc 2
    This test pins that both are still PRESENT, since a survivor is otherwise
    indistinguishable from a guard nobody needs.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert "[[ $s =~ ^[a-z0-9][a-z0-9._-]*$ ]]" in src, (
        "the whole-string pattern guard is gone. If it was replaced by a "
        "line-based `grep`, a slug whose FIRST line is legal passes validation "
        "and the run exits 0 DEGRADED instead of rc 2.")
    assert 'git check-ref-format "${CLAIM_NS}${s}"' in src, (
        "the check-ref-format backstop is gone — it is the only guard that "
        "catches a multi-line slug if the pattern guard ever regresses.")


def test_an_unknown_option_is_a_usage_error(tmp_path):
    r = _run("--nonsense", repo=tmp_path)
    assert r.returncode == RC_USAGE, r.stderr
    assert "unknown option" in r.stderr, r.stderr


def test_no_arguments_prints_usage_rather_than_claiming_something(tmp_path):
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=_env())
    assert r.returncode == RC_USAGE
    assert "claim-work" in r.stderr


# ── the script's own contract, read off the file ──────────────────────────────

def test_the_script_documents_every_rc_it_can_return():
    """A distinct exit code nobody can look up is not a documented contract."""
    src = SCRIPT.read_text(encoding="utf-8")
    for rc in (RC_USAGE, RC_TAKEN, RC_TAKEN_STALE, RC_MINE, RC_DEGRADED_STRICT):
        assert re.search(rf"^#\s+{rc}\s", src, re.M), (
            f"rc {rc} is returned but has no line in the EXIT CODES block")


def test_the_script_is_syntactically_valid_bash():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_script_is_executable():
    """It is named as a command in claude/RULES.md and deployed onto PATH as
    `~/.local/bin/claim-work`; a non-executable mode there is a dead pointer."""
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_the_cross_runtime_pointer_is_in_the_one_file_both_runtimes_load():
    """🔴 THE COLLIDING PARTY WAS AN `opencode` RUN, NOT A CLAUDE SESSION.

    A fix reachable only from a Claude skill does not cover the collision that
    happened. `claude/RULES.md` is the ONE file both runtimes receive — Claude
    Code imports it, and `nix/home.nix` concatenates it into opencode's
    `~/.config/opencode/AGENTS.md`. If the command is not named there, opencode
    never learns it exists, and this whole mechanism covers one runtime of two.
    """
    rules = (REPO_ROOT / "claude" / "RULES.md").read_text(encoding="utf-8")
    assert "claim-work" in rules, (
        "claude/RULES.md does not name `claim-work`. It is the only file both "
        "Claude Code and opencode load, so a mechanism absent from it is absent "
        "from the runtime that actually collided."
    )
    home_nix = (REPO_ROOT / "nix" / "home.nix").read_text(encoding="utf-8")
    assert "opencode/AGENTS.md" in home_nix and "RULES.md" in home_nix, (
        "the concatenation that carries RULES.md into opencode's AGENTS.md is no "
        "longer visible in nix/home.nix — the cross-runtime claim above may be stale"
    )
    assert 'home.file.".local/bin/claim-work"' in home_nix, (
        "claim-work is not deployed onto PATH by nix/home.nix, so the bare "
        "command named in RULES.md would not resolve in either runtime"
    )


def test_the_prose_that_used_to_carry_the_rule_now_points_at_the_command():
    """🔴 ONE SOURCE OF TRUTH. A rule and a tool that can drift apart is how the
    prose fix failed the first time — it was live six minutes before the next
    collision. Every place that described the hazard must now name the command.
    """
    for rel in ("claude/skills/handoff/SKILL.md",
                "claude/skills/handoff/reference/shared-queue.md",
                "claude/skills/resume/SKILL.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "claim-work" in text, (
            f"{rel} still describes the shared-queue hazard without naming "
            f"`claim-work`, so it is a second source of truth that can drift")


# ── 🔴 FINDING 1: the claim namespace is GLOBAL, not per-origin ───────────────

def test_the_claim_namespace_is_global_not_per_origin(tmp_path):
    """🔴 THE REGRESSION COVER FOR AN ENTIRELY INERT MECHANISM.

    Measured 2026-08-26 on the pre-fix script: the same canonical slug claimed
    from two repos with two different `origin`s BOTH returned rc 0 CLAIMED, one
    ref on each origin, no warning. The remote came from `$PWD`, so the namespace
    was per-origin — and the queue it locks is not. The incident it exists for is
    exactly that shape: a devrc handoff doc ranking work whose PRs were
    homelab-infra `#386`/`#388`/`#389`.

    So: two sessions, two different cwds, two different `origin`s, ONE canonical
    remote taken from the script's own repo. First rc 0, second rc 10.
    """
    canonical = _bare_origin(tmp_path, "canonical.git")
    script = _install_script(tmp_path, canonical)

    # Two sessions in two DIFFERENT repos with two DIFFERENT origins — the exact
    # setup that produced two winners before the fix.
    other_a = _bare_origin(tmp_path, "origin-a.git")
    other_b = _bare_origin(tmp_path, "origin-b.git")
    a = _session(tmp_path, other_a, "Session A", "a")
    b = _session(tmp_path, other_b, "Session B", "b")

    # No --repo, no --remote, no DEVRC_CLAIM_REMOTE: the canonical default.
    env = _env(DEVRC_CLAIM_REMOTE="")

    first = _run("shared-item", "--subject", "the ranked item",
                 script=script, cwd=a, env=env)
    assert first.returncode == RC_OK, f"{first.stdout}\n{first.stderr}"
    assert "CLAIMED" in first.stdout, first.stdout

    second = _run("shared-item", "--subject", "the same ranked item",
                  script=script, cwd=b, env=env)
    assert second.returncode == RC_TAKEN, (
        f"a second session in a DIFFERENT repo with a DIFFERENT origin was not "
        f"refused (rc={second.returncode}) — the claim namespace is following the "
        f"cwd again and the mechanism is inert cross-repo\n"
        f"{second.stdout}\n{second.stderr}"
    )

    # And exactly ONE ref exists, on the CANONICAL remote — not one per origin.
    assert f"{CLAIM_NS}shared-item" in _remote_refs(canonical)
    for stray, label in ((other_a, "origin-a"), (other_b, "origin-b")):
        assert not [r for r in _remote_refs(stray) if r.startswith(CLAIM_NS)], (
            f"a claim ref landed on {label}, the CWD's origin — the namespace is "
            f"still per-repo: {_remote_refs(stray)}")


def test_the_canonical_remote_survives_the_symlink_the_command_is_deployed_as(tmp_path):
    """`~/.local/bin/claim-work` is an `mkOutOfStoreSymlink`, so the resolution
    has to follow the link chain. Invoked THROUGH the symlink, from an unrelated
    cwd, it must still land on the linked-to repo's origin."""
    canonical = _bare_origin(tmp_path, "canonical.git")
    real = _install_script(tmp_path, canonical)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    link = bindir / "claim-work"
    link.symlink_to(real)

    elsewhere = _session(tmp_path, _bare_origin(tmp_path, "unrelated.git"),
                         "Session A", "elsewhere")
    r = _run("via-symlink", script=link, cwd=elsewhere,
             env=_env(DEVRC_CLAIM_REMOTE=""))
    assert r.returncode == RC_OK, f"{r.stdout}\n{r.stderr}"
    assert f"{CLAIM_NS}via-symlink" in _remote_refs(canonical), (
        "the claim did not land on the canonical remote — `readlink -f` is no "
        "longer resolving the deployed symlink to its repo")


def test_an_unresolvable_canonical_remote_degrades_rather_than_using_the_cwd(tmp_path):
    """🔴 NO CWD FALLBACK. A fallback is not a graceful degradation here — it
    reinstates the per-origin namespace and reports CLAIMED while doing it, which
    is strictly worse than saying "I could not find out". So: script copied
    somewhere that is not a git repo, cwd a perfectly good repo WITH an origin ⇒
    degrade, and nothing lands on that origin."""
    cwd_origin = _bare_origin(tmp_path, "cwd-origin.git")
    session = _session(tmp_path, cwd_origin, "Session A", "s")

    loose = tmp_path / "loose"
    loose.mkdir()
    script = loose / "claim-work.sh"
    shutil.copy2(SCRIPT, script)

    env = _env(DEVRC_CLAIM_REMOTE="")
    lenient = _run("orphaned", script=script, cwd=session, env=env)
    assert lenient.returncode == RC_OK, (
        f"it must still fail OPEN: rc={lenient.returncode}\n{lenient.stderr}")
    assert "DEGRADED" in lenient.stderr, lenient.stderr
    assert "canonical" in lenient.stderr.lower(), (
        f"the degradation does not say WHICH remote it could not resolve:\n"
        f"{lenient.stderr}")
    assert not [r for r in _remote_refs(cwd_origin) if r.startswith(CLAIM_NS)], (
        f"it fell back to the CWD's origin — that is the bug this test exists "
        f"for: {_remote_refs(cwd_origin)}")

    strict = _run("--strict", "orphaned", script=script, cwd=session, env=env)
    assert strict.returncode == RC_DEGRADED_STRICT, (
        f"--strict must surface it as rc {RC_DEGRADED_STRICT}, "
        f"got {strict.returncode}\n{strict.stderr}")


def test_the_environment_override_beats_the_canonical_remote_but_not_the_flags(tmp_path):
    """The documented precedence, measured at three points rather than asserted:
    `--remote` > `--repo` > `DEVRC_CLAIM_REMOTE` > canonical. Only measuring one
    of them would leave the ordering a claim about code nobody exercised."""
    canonical = _bare_origin(tmp_path, "canonical.git")
    script = _install_script(tmp_path, canonical)
    by_env = _bare_origin(tmp_path, "by-env.git")
    by_repo_origin = _bare_origin(tmp_path, "by-repo.git")
    by_flag = _bare_origin(tmp_path, "by-flag.git")
    repo = _session(tmp_path, by_repo_origin, "Session A", "s")

    def where(origin: Path) -> list:
        return [r for r in _remote_refs(origin) if r.startswith(CLAIM_NS)]

    assert _run("e", script=script, cwd=repo,
                env=_env(DEVRC_CLAIM_REMOTE=str(by_env))).returncode == RC_OK
    assert where(by_env) and not where(canonical), "DEVRC_CLAIM_REMOTE was ignored"

    assert _run("r", repo=repo, script=script, cwd=repo,
                env=_env(DEVRC_CLAIM_REMOTE=str(by_env))).returncode == RC_OK
    assert where(by_repo_origin), "--repo did not beat DEVRC_CLAIM_REMOTE"
    assert f"{CLAIM_NS}r" not in _remote_refs(by_env)

    assert _run("f", "--remote", str(by_flag), repo=repo, script=script, cwd=repo,
                env=_env(DEVRC_CLAIM_REMOTE=str(by_env))).returncode == RC_OK
    assert where(by_flag), "--remote did not beat --repo"
    assert f"{CLAIM_NS}f" not in _remote_refs(by_repo_origin)


# ── 🔴 FINDING 2: a claim that LANDS must never be reported UNCLAIMED ─────────

def _env_with_git_config(pairs, **overrides) -> dict:
    """Append `-c`-equivalent config pairs to the hermetic env without clobbering
    the maintenance pins already occupying `GIT_CONFIG_KEY_0/1`. git reads exactly
    `GIT_CONFIG_COUNT` pairs, so the count has to be extended, not replaced."""
    env = _env(**overrides)
    n = int(env.get("GIT_CONFIG_COUNT", "0"))
    for key, value in pairs:
        env[f"GIT_CONFIG_KEY_{n}"] = key
        env[f"GIT_CONFIG_VALUE_{n}"] = value
        n += 1
    env["GIT_CONFIG_COUNT"] = str(n)
    return env


def test_a_push_that_lands_and_then_fails_client_side_reports_CLAIMED(tmp_path):
    """🔴 THE STUCK-LOCK-WITH-NO-HOLDER CASE. The push reaches the server, the ref
    is created, and the client then fails afterwards. Pre-fix the script compared
    the remote's sha to ours, found them EQUAL, and fell through to
    `"…does not exist there — not a collision"` ⇒ exit 0 "Proceeding UNCLAIMED".
    Result: a live claim whose holder believes it holds nothing, blocking the item
    for the whole TTL with nobody to release it.

    Injected hermetically by pointing `remote.origin.receivepack` at a wrapper
    that runs the real `git receive-pack` — so the ref genuinely lands — and then
    exits 1. Verified 2026-08-26 that this does put the ref on origin while
    `git push` returns non-zero.

    🔴 It also kills the `awk 'NR==1{print $1}'` → `$2` mutant in
    `remote_ref_sha`: field 2 is the REF NAME, which can never equal our commit's
    sha, so this "did my push land?" branch would misreport under it.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    # `mockbin.write_exec` owns the shebang: a runtime-written `#!/usr/bin/env`
    # stub execs on the dev host and ENOENTs in the nix sandbox, which is a
    # repo-wide guard (`scripts/tests/test_runtime_shebangs.py`).
    wrapper = mockbin.write_exec(tmp_path / "receive-pack-then-fail.sh",
                                 'git receive-pack "$1"\nexit 1\n')

    env = _env_with_git_config([("remote.origin.receivepack", str(wrapper))])
    r = _run("landed-anyway", "--subject", "the item", repo=a, env=env)

    landed = _remote_refs(origin).get(f"{CLAIM_NS}landed-anyway")
    assert landed, (
        "the injection did not put the ref on origin, so this test is not "
        "exercising the case it is named for — re-check the receivepack wrapper")
    assert r.returncode == RC_OK, f"{r.stdout}\n{r.stderr}"
    assert "CLAIMED" in r.stdout, (
        f"a claim whose ref LANDED was not reported as claimed:\n"
        f"{r.stdout}\n{r.stderr}")
    assert "UNCLAIMED" not in r.stderr, (
        f"the holder of a live claim was told it holds nothing — the ref is on "
        f"origin at {landed} and will block this item for the whole TTL:\n"
        f"{r.stdout}\n{r.stderr}")
    assert landed in r.stdout, (
        f"the reported sha is not the sha on origin — `remote_ref_sha` is not "
        f"returning the object name:\n{r.stdout}")


# ── 🔴 FINDING 4: --release / --steal are destructive and need an owner ───────

def test_release_refuses_another_sessions_live_claim_unless_forced(tmp_path):
    """🔴 Measured 2026-08-26 before the gate existed: session B released session
    A's ZERO-SECOND-OLD live claim, rc 0, silently. rc 10 prints "DO NOT start
    this item" with `--release` one flag away, so without a gate the refusal is
    advice, not a lock.

    Ownership cannot be the git AUTHOR — both hosts and every agent on one host
    share one identity — so it is keyed off the host + cwd-id the claim commit
    records. Here both sessions carry the same hermetic identity on purpose, so
    only that token can produce the refusal.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")

    assert _run("held", "--subject", "A is working on this", repo=a).returncode == RC_OK
    ref_before = _remote_refs(origin)[f"{CLAIM_NS}held"]

    refused = _run("--release", "held", repo=b)
    assert refused.returncode == RC_TAKEN, (
        f"session B released a live claim it does not hold (rc={refused.returncode})"
        f"\n{refused.stdout}\n{refused.stderr}")
    assert "REFUSED" in refused.stdout, refused.stdout
    assert "--force" in refused.stdout, (
        f"the refusal does not say how to override it deliberately:\n{refused.stdout}")
    assert _remote_refs(origin).get(f"{CLAIM_NS}held") == ref_before, (
        "the ref moved despite the refusal")

    # …and the owner is still allowed, so the gate is not simply "always refuse".
    assert _run("--release", "held", repo=a).returncode == RC_OK, (
        "the OWNER was refused too — the gate is refusing on something other "
        "than ownership")


def test_release_with_force_overrides_the_ownership_gate(tmp_path):
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")
    assert _run("held", repo=a).returncode == RC_OK
    forced = _run("--release", "held", "--force", repo=b)
    assert forced.returncode == RC_OK, f"{forced.stdout}\n{forced.stderr}"
    assert f"{CLAIM_NS}held" not in _remote_refs(origin)
    assert "--force" in forced.stderr, (
        f"a forced override of somebody else's claim was silent:\n{forced.stderr}")


def test_steal_refuses_another_sessions_live_claim_unless_forced(tmp_path):
    """`usage()` calls `--steal` the verb for a "stale/abandoned" claim and the
    design doc says taking over is "never automatic". Measured: nothing enforced
    either — B stole A's 0-second-old claim, rc 0."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")

    assert _run("held", "--subject", "A is working on this", repo=a).returncode == RC_OK
    before = _remote_refs(origin)[f"{CLAIM_NS}held"]

    refused = _run("--steal", "held", "--subject", "mine now", repo=b)
    assert refused.returncode == RC_TAKEN, (
        f"session B stole a live claim (rc={refused.returncode})\n"
        f"{refused.stdout}\n{refused.stderr}")
    assert "REFUSED" in refused.stdout, refused.stdout
    assert _remote_refs(origin)[f"{CLAIM_NS}held"] == before, "the ref was overwritten"

    forced = _run("--steal", "held", "--subject", "mine now", "--force", repo=b)
    assert forced.returncode == RC_OK, f"{forced.stdout}\n{forced.stderr}"
    assert _remote_refs(origin)[f"{CLAIM_NS}held"] != before


def test_a_stale_claim_can_still_be_released_by_anyone_without_force(tmp_path):
    """The gate must not turn a stale ref back into a permanent block — that is
    the failure the TTL exists to prevent. Two ages, one fixture: the SAME claim
    is refused while live and released once past the TTL, so the staleness
    threshold is demonstrably what opened the gate."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")

    old = int(time.time()) - 30 * 86400
    aged = _env(GIT_AUTHOR_DATE=f"{old} +0000", GIT_COMMITTER_DATE=f"{old} +0000")
    assert _run("abandoned", "--subject", "gone quiet", repo=a, env=aged).returncode == RC_OK

    live = _run("--release", "abandoned", repo=b, env=_env(DEVRC_CLAIM_TTL_DAYS="60"))
    assert live.returncode == RC_TAKEN, (
        f"with a 60-day TTL this 30-day claim is LIVE and another session must "
        f"not release it (rc={live.returncode})\n{live.stdout}")

    stale = _run("--release", "abandoned", repo=b)
    assert stale.returncode == RC_OK, (
        f"a claim past the default TTL could not be released by another session, "
        f"so a stale ref blocks the item forever\n{stale.stdout}\n{stale.stderr}")
    assert f"{CLAIM_NS}abandoned" not in _remote_refs(origin)


def test_a_claim_whose_owner_cannot_be_read_is_not_yours_by_default(tmp_path):
    """"Could not find out" must not authorise a destructive write on somebody
    else's lock. The ref is visible to `ls-remote` and its commit is NOT
    fetchable, which is also the reachable branch a mutation sweep found
    permitting a collision (`return "$RC_TAKEN"` → `return 0`)."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    ghost = origin / "refs" / "heads" / "claim"
    ghost.mkdir(parents=True, exist_ok=True)
    (ghost / "phantom").write_text("0123456789abcdef0123456789abcdef01234567\n")

    r = _run("--release", "phantom", repo=a)
    assert r.returncode == RC_TAKEN, (
        f"a claim whose owner could not be read was released anyway "
        f"(rc={r.returncode})\n{r.stdout}\n{r.stderr}")
    assert "cannot read" in r.stdout, r.stdout
    assert f"{CLAIM_NS}phantom" in _remote_refs(origin), "the ref was deleted anyway"


def test_a_claim_whose_commit_cannot_be_read_is_still_reported_TAKEN(tmp_path):
    """🔴 A REACHABLE, COLLISION-PERMITTING MUTANT. `report_existing`'s
    unreadable-claim branch returns rc 10; a sweep found `return 0` surviving the
    whole suite, i.e. an existing claim reported as free. The ref exists (
    `ls-remote` sees it) and the object does not, so the fetch fails — measured
    2026-08-26."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    ghost = origin / "refs" / "heads" / "claim"
    ghost.mkdir(parents=True, exist_ok=True)
    (ghost / "phantom").write_text("0123456789abcdef0123456789abcdef01234567\n")

    check = _run("--check", "phantom", repo=a)
    assert check.returncode == RC_TAKEN, (
        f"a claim ref that exists on origin but cannot be read was not reported "
        f"TAKEN (rc={check.returncode})\n{check.stdout}\n{check.stderr}")
    assert "ALREADY CLAIMED" in check.stdout, check.stdout
    assert "FREE" not in check.stdout, check.stdout

    claim = _run("phantom", repo=a)
    assert claim.returncode == RC_TAKEN, (
        f"claiming over an unreadable existing claim was not refused "
        f"(rc={claim.returncode})\n{claim.stdout}\n{claim.stderr}")


# ── 🔴 FINDING 5 / 9: the refusal must name a party that can discriminate ─────

def test_the_refusal_names_the_session_not_only_the_shared_identity(tmp_path):
    """`%an <%ae>` is the SAME string for every session on both hosts, so a
    refusal that prints only the author names a party the reader cannot tell
    apart from themselves. host + cwd-id can, and the claim commit already
    carried them."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")
    assert _run("topic-1", "--subject", "x", repo=a).returncode == RC_OK

    second = _run("topic-1", repo=b)
    assert second.returncode == RC_TAKEN, second.stdout
    assert re.search(r"where:\s+host \S+, owner-id [0-9a-f]{6,}", second.stdout), (
        f"the refusal does not say WHERE the holder is, so with one shared git "
        f"identity it names nothing the reader can act on:\n{second.stdout}")

    # …and the SAME session is told it already holds it, which is the usability
    # half: without it, "who: Same Identity" is indistinguishable from your own.
    own = _run("--check", "topic-1", repo=a)
    assert own.returncode == RC_MINE
    assert "THIS SESSION" in own.stdout, (
        f"a session checking its OWN claim is not told so:\n{own.stdout}")


def test_the_machine_prefix_never_reaches_the_human_field(tmp_path):
    """`claim(<slug>): ` is for the ref, not for a reader. It used to be printed
    verbatim in `what:`, and a claim with no subject printed the bare machine
    string as if it were one."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    b = _session(tmp_path, origin, "Session B", "b")

    assert _run("with-subj", "--subject", "right-size the requests", repo=a).returncode == RC_OK
    r = _run("--check", "with-subj", repo=b)
    what = [l for l in r.stdout.splitlines() if l.strip().startswith("what:")]
    assert what and "claim(with-subj)" not in what[0], (
        f"the machine prefix leaked into the human field: {what}")
    assert "right-size the requests" in what[0], what

    assert _run("no-subj", repo=a).returncode == RC_OK
    r2 = _run("--check", "no-subj", repo=b)
    what2 = [l for l in r2.stdout.splitlines() if l.strip().startswith("what:")]
    assert what2 and "no --subject" in what2[0], (
        f"a claim with no subject printed the machine string as if it were one: {what2}")
    listed = _run("--list", repo=b)
    assert "claim(no-subj)" not in listed.stdout, (
        f"--list leaked the machine prefix:\n{listed.stdout}")


# ── 🔴 FINDING 9: the mutants that survived a fully green suite ───────────────

def test_two_claims_that_would_be_byte_identical_still_collide(tmp_path):
    """🔴 THE NONCE, WHICH WAS LOAD-BEARING WITH ZERO COVERAGE. A sweep replaced
    it with a constant and the whole suite stayed green.

    Without it, two claims that agree on identity, message, cwd and SECOND are
    byte-identical, so they have the SAME sha — and pushing the sha a ref already
    holds is "Everything up-to-date", exit 0. The second session then prints
    CLAIMED for an item it does not hold, which is the collision this tool exists
    to stop, produced by the tool itself.

    Every other input is pinned identical here on purpose (same repo ⇒ same
    identity and same cwd-id; `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` fixed ⇒ same
    second; no `--subject` ⇒ same message), so the nonce is the ONLY remaining
    difference and the assertion cannot pass for another reason.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    fixed = f"{int(time.time())} +0000"
    env = _env(GIT_AUTHOR_DATE=fixed, GIT_COMMITTER_DATE=fixed)

    first = _run("twin", repo=a, env=env)
    assert first.returncode == RC_OK, f"{first.stdout}\n{first.stderr}"
    second = _run("twin", repo=a, env=env)
    # rc 12 (yours), because pinning every input identical necessarily means the
    # SAME owner token. What the mutant produces is rc 0 + "CLAIMED", so the pair
    # rc/text below is still what discriminates; only the expected code moved.
    assert second.returncode == RC_MINE, (
        f"a second invocation with every input pinned identical was told it had "
        f"CLAIMED the item (rc={second.returncode}). The two claim commits share "
        f"a sha, so the push was 'Everything up-to-date' — the nonce is not "
        f"making them differ.\n{second.stdout}\n{second.stderr}"
    )
    assert "ALREADY CLAIMED" in second.stdout, (
        f"the second invocation reported a fresh CLAIM:\n{second.stdout}")


def test_a_claim_exactly_at_the_ttl_boundary_reads_LIVE_not_stale(tmp_path):
    """🔴 THE BOUNDARY, WHICH `-gt` → `-ge` SURVIVED. The two comparisons differ
    at EXACTLY one age, so no approximate fixture can see the mutant.

    An exact hit is made deterministic by the clamp rather than by luck: a
    FUTURE-dated claim (real clock skew between the two hosts) yields a negative
    age, which clamps to exactly 0 — and with `DEVRC_CLAIM_TTL_DAYS=0` the
    threshold is also 0, so `age > threshold` and `age >= threshold` disagree.
    Measured at the boundary AND one second past it, so the claim carries its own
    scope.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    b = _session(tmp_path, origin, "Session B", "b")

    future = int(time.time()) + 3600
    ahead = _env(GIT_AUTHOR_DATE=f"{future} +0000",
                 GIT_COMMITTER_DATE=f"{future} +0000")
    assert _run("skewed", "--subject", "from a host whose clock is ahead",
                repo=a, env=ahead).returncode == RC_OK

    at_boundary = _run("--check", "skewed", repo=b,
                       env=_env(DEVRC_CLAIM_TTL_DAYS="0"))
    assert at_boundary.returncode == RC_TAKEN, (
        f"a claim whose age is EXACTLY the threshold read STALE "
        f"(rc={at_boundary.returncode}) — the comparison is >= where it must be > "
        f"\n{at_boundary.stdout}")
    assert "STALE" not in at_boundary.stdout, at_boundary.stdout

    # The other side of the boundary, from the same threshold: one second past.
    past = int(time.time()) - 1
    behind = _env(GIT_AUTHOR_DATE=f"{past} +0000", GIT_COMMITTER_DATE=f"{past} +0000")
    assert _run("just-past", repo=a, env=behind).returncode == RC_OK
    over = _run("--check", "just-past", repo=b, env=_env(DEVRC_CLAIM_TTL_DAYS="0"))
    assert over.returncode == RC_TAKEN_STALE, (
        f"one second past a zero threshold did NOT read stale (rc={over.returncode})"
        f", so the assertion above is not discriminating\n{over.stdout}")


def test_the_ttl_fallback_is_seven_days_measured_on_both_sides(tmp_path):
    """`DEFAULT_TTL_DAYS=7` → `70` survived a green suite, because nothing ever
    measured the fallback's VALUE — only that it was not zero and did not abort.
    So: with a garbage TTL, a 6-day claim must read LIVE and an 8-day claim must
    read STALE. A wrong magnitude fails one of the two whichever way it moves."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    b = _session(tmp_path, origin, "Session B", "b")
    garbage = _env(DEVRC_CLAIM_TTL_DAYS="week")

    for slug, days, want in (("six", 6, RC_TAKEN), ("eight", 8, RC_TAKEN_STALE)):
        when = int(time.time()) - days * 86400
        aged = _env(GIT_AUTHOR_DATE=f"{when} +0000", GIT_COMMITTER_DATE=f"{when} +0000")
        assert _run(slug, repo=a, env=aged).returncode == RC_OK
        r = _run("--check", slug, repo=b, env=garbage)
        assert r.returncode == want, (
            f"a {days}-day-old claim read rc {r.returncode} under the fallback "
            f"TTL, expected {want}. The default is not 7 days.\n{r.stdout}\n{r.stderr}")


def test_list_flags_a_stale_claim_and_leaves_a_fresh_one_unflagged(tmp_path):
    """`--list`'s `[STALE]` comparison inverted SURVIVED a green suite — nothing
    asserted the marker at all. Two ages in ONE listing, so an inversion fails
    whichever way it goes."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    old = int(time.time()) - 30 * 86400
    aged = _env(GIT_AUTHOR_DATE=f"{old} +0000", GIT_COMMITTER_DATE=f"{old} +0000")

    assert _run("ancient", "--subject", "gone quiet", repo=a, env=aged).returncode == RC_OK
    assert _run("recent", "--subject", "started just now", repo=a).returncode == RC_OK

    r = _run("--list", repo=a)
    assert r.returncode == RC_OK, r.stderr
    rows = {line.split()[0]: line for line in r.stdout.splitlines()
            if line[:1].isalnum() and " ago " in line}
    assert "ancient" in rows and "recent" in rows, r.stdout
    assert "[STALE]" in rows["ancient"], (
        f"a 30-day-old claim is not flagged stale in --list:\n{r.stdout}")
    assert "[STALE]" not in rows["recent"], (
        f"a claim made seconds ago is flagged stale in --list:\n{r.stdout}")


def test_a_garbage_network_timeout_falls_back_instead_of_degrading_every_call(tmp_path):
    """🔴 `DEVRC_CLAIM_TIMEOUT` got none of the validation the TTL gets, and
    garbage there is WORSE: `timeout <junk> git …` exits 125 without running git,
    so EVERY network call reads as a failure and the tool silently degrades to
    "proceeding UNCLAIMED" on every single invocation — a lock that has stopped
    locking while reporting exit 0."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    r = _run("topic-1", repo=a, env=_env(DEVRC_CLAIM_TIMEOUT="20 seconds"))
    assert "not a `timeout` duration" in r.stderr, (
        f"a garbage timeout was not rejected:\n{r.stderr}")
    assert r.returncode == RC_OK and "CLAIMED" in r.stdout, (
        f"a garbage timeout degraded the run instead of falling back "
        f"(rc={r.returncode})\n{r.stdout}\n{r.stderr}")
    assert f"{CLAIM_NS}topic-1" in _remote_refs(origin)
    # POSITIVE CONTROL: a VALID duration must not trip the warning, or the check
    # above is satisfied by a message that fires unconditionally.
    ok = _run("topic-2", repo=a, env=_env(DEVRC_CLAIM_TIMEOUT="5s"))
    assert "not a `timeout` duration" not in ok.stderr, ok.stderr
    assert ok.returncode == RC_OK, ok.stderr


def test_slug_for_rejects_a_flag_swallowed_as_the_doc_path():
    """`--slug-for --strict` derived the slug `strict` and exited 0 — a usage
    error reported as a successful answer, which the caller then claims."""
    r = _run("--slug-for", "--strict")
    assert r.returncode == RC_USAGE, (
        f"a flag swallowed as the doc path produced rc {r.returncode} and the "
        f"output {r.stdout.strip()!r}")
    assert "strict" not in r.stdout, r.stdout


# ── 🔴 FINDING 7: the operator's global hooks run on every claim push ─────────

def test_a_global_pre_push_hook_cannot_make_the_lock_inert(tmp_path):
    """🔴 THE SURFACE THE WHOLE SUITE WAS BLIND TO. Every other test here runs
    under `GIT_CONFIG_GLOBAL=/dev/null`, and that is exactly the file carrying
    `core.hooksPath`. The scratch repo is `git init`ed, so it inherits it —
    measured 2026-08-26: a global `pre-push` FIRES on a claim push, and one that
    exits non-zero makes the push fail, which the script turns into a degraded
    exit 0 "proceeding UNCLAIMED". A blocking hook therefore made this lock
    silently inert, with no test able to see it.

    So this test deliberately does NOT neutralise the global config: it points
    `$HOME` at a fixture `.gitconfig` with a real blocking hook.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")

    hooks = tmp_path / "global-hooks"
    hooks.mkdir()
    fired = tmp_path / "hook-fired"
    hook = mockbin.write_exec(
        hooks / "pre-push",
        f'echo fired >> "{fired}"\n'
        'echo "global pre-push says no" >&2\n'
        "exit 1\n")

    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        f"[core]\n\thooksPath = {hooks}\n[user]\n\tname = Op\n\temail = op@localhost\n")

    env = _env(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    # 🔴 The point of the test: DROP the pin that hides the hazard.
    env.pop("GIT_CONFIG_GLOBAL", None)

    # NEGATIVE CONTROL — the hook must actually be live in this environment, or a
    # green here says nothing. A plain push from the fixture repo has to fail.
    tree = _git("-C", str(a), "mktree", input="", env=env).stdout.strip()
    probe = _git("-C", str(a), "commit-tree", tree, input="probe", env=env).stdout.strip()
    blocked = _git("-C", str(a), "push", "origin", f"{probe}:refs/heads/probe",
                   env=env, check=False)
    assert blocked.returncode != 0 and fired.exists(), (
        "the fixture global pre-push hook did not fire, so this test cannot see "
        f"the hazard it is named for:\n{blocked.stderr}")
    fired.unlink()

    r = _run("hooked", "--subject", "x", repo=a, env=env)
    assert r.returncode == RC_OK, f"{r.stdout}\n{r.stderr}"
    assert f"{CLAIM_NS}hooked" in _remote_refs(origin), (
        f"the operator's global pre-push hook blocked the claim push, so the "
        f"lock is inert on a host that has one:\n{r.stdout}\n{r.stderr}")
    assert "CLAIMED" in r.stdout, r.stdout
    assert "DEGRADED" not in r.stderr, r.stderr
    assert not fired.exists(), (
        "the global hook RAN during the claim — `-c core.hooksPath` is not being "
        "applied to every git call in the scratch repo")


def test_git_never_prompts_for_credentials_on_a_claim(tmp_path):
    """`GIT_TERMINAL_PROMPT=0` -> `=1` survived a green suite. A credential prompt
    in an agent's session is a hang, and a hang in `/resume` is the one thing this
    tool promises never to do.

    OBSERVED, NOT ASSERTED - and observed in the process that actually does the
    talking. `remote.origin.receivepack` points at a wrapper that DUMPS ITS OWN
    ENVIRONMENT before exec'ing the real `git receive-pack`; that wrapper is a
    child of the very `git push` the claim is made with, so what it records is the
    environment the network call ran under, not what the source says.

    Why not a real 401: `scripts/run-tests.sh` and `testlib/nogit_plugin.py` pin
    `GIT_ALLOW_PROTOCOL=file` repo-wide so no fixture can reach a real remote.
    Relaxing that to serve an HTTP fixture would trade a standing safety guard
    for one test. This route needs no relaxation.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    dump = tmp_path / "push-env.txt"
    wrapper = mockbin.write_exec(
        tmp_path / "receive-pack-recording-env.sh",
        f'env > "{dump}"\n'
        'exec git receive-pack "$1"\n')

    env = _env_with_git_config([("remote.origin.receivepack", str(wrapper))])
    r = _run("topic-1", repo=a, env=env)
    assert r.returncode == RC_OK, f"{r.stdout}\n{r.stderr}"
    assert f"{CLAIM_NS}topic-1" in _remote_refs(origin), (
        "the recording wrapper broke the push, so nothing was observed")

    recorded = dict(
        line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line)
    assert recorded.get("GIT_TERMINAL_PROMPT") == "0", (
        f"the git process that performs the claim push ran with "
        f"GIT_TERMINAL_PROMPT={recorded.get('GIT_TERMINAL_PROMPT')!r} - an agent "
        f"session can be left waiting for a password it will never receive")
    # POSITIVE CONTROL for the recorder itself: it must be able to SEE a variable
    # the script exports, or the assertion above is indistinguishable from a dump
    # that captured nothing.
    assert recorded.get("GIT_SSH_COMMAND", "").startswith("ssh "), (
        f"the environment dump did not capture the variables the script exports, "
        f"so it cannot testify about GIT_TERMINAL_PROMPT either: "
        f"{sorted(k for k in recorded if k.startswith('GIT_'))}")
    assert "GIT_ASKPASS" not in recorded, (
        "GIT_ASKPASS survived into the push; an askpass helper is consulted "
        "BEFORE GIT_TERMINAL_PROMPT is honoured, so it re-opens the hang")


def test_the_scratch_dir_cleanup_survives_a_kill_with_the_EXIT_trap_alone():
    """AN INVARIANT GUARD, LABELLED AS ONE — it is NOT regression coverage.

    An audit asked for `trap cleanup EXIT INT TERM HUP`, on the theory that a
    killed run leaks its `mktemp -d`. MEASURED 2026-08-26 on bash 5.3.15, with a
    run parked mid-push in a hanging `receive-pack` and SIGTERMed: with `trap
    cleanup EXIT` ALONE it exited rc -15 and left ZERO `claim-work.*` behind —
    bash's terminating-signal handler runs the EXIT trap. The premise was false.

    🔴 And the proposed fix was worse than useless: a `TERM` handler RETURNS
    rather than exits, so the same run with the signal traps added deleted $WS
    and carried on using it, exiting **0** — a killed resume reporting success.
    Measured too. So this pins the trap list as EXIT-only, and any future
    widening must re-raise the signal instead of falling through.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    traps = re.findall(r"^trap\s+cleanup\s+(.*)$", src, re.M)
    assert traps == ["EXIT"], (
        f"the cleanup trap list is {traps!r}. A bare signal trap RETURNS rather "
        f"than exiting, so the script would run on over a deleted scratch repo "
        f"and a killed run would report exit 0. If this must widen, the handler "
        f"has to re-raise: `trap - TERM; cleanup; kill -s TERM $$`.")


# ── 🔴 the prose ledgers: a claim in one doc and not the others is drift ──────

DESIGN_DOC = REPO_ROOT / "claudedocs" / "design-claim-by-push.md"
SHARED_QUEUE = REPO_ROOT / "claude" / "skills" / "handoff" / "reference" / "shared-queue.md"
RESUME_SKILL = REPO_ROOT / "claude" / "skills" / "resume" / "SKILL.md"
RULES_MD = REPO_ROOT / "claude" / "RULES.md"


def _resume_step_6() -> str:
    text = RESUME_SKILL.read_text(encoding="utf-8")
    start = text.index("\n6. ")
    end = text.index("Then wait for direction.", start)
    return text[start:end]


def _normalised(text: str) -> str:
    return " ".join(text.split())


def test_every_surface_says_the_claim_namespace_is_GLOBAL(tmp_path):
    """🔴 THE ABSENCE FROM EVERY ONE OF THESE WAS HALF THE FINDING. The remote
    came from `$PWD` and not one surface said so, so nobody reading any of them
    could have known the same slug was claimable once per remote.

    The `usage()` leg is checked BEHAVIOURALLY — by running `--help` — because
    that is the copy a caller actually reads, and a string present in the file but
    outside the heredoc would satisfy a grep and help nobody.
    """
    helped = _run("--help")
    assert helped.returncode == RC_OK, helped.stderr
    surfaces = {
        "claim-work --help (usage)": helped.stderr + helped.stdout,
        "scripts/claim-work.sh (header)": SCRIPT.read_text(encoding="utf-8"),
        "claude/RULES.md": RULES_MD.read_text(encoding="utf-8"),
        "resume/SKILL.md step 6": _resume_step_6(),
        "handoff/reference/shared-queue.md": SHARED_QUEUE.read_text(encoding="utf-8"),
        "claudedocs/design-claim-by-push.md": DESIGN_DOC.read_text(encoding="utf-8"),
    }
    for name, text in surfaces.items():
        assert "GLOBAL" in text, (
            f"{name} does not say the claim namespace is GLOBAL. It was per-cwd "
            f"until 2026-08-26 and the mechanism was inert cross-repo; a reader "
            f"of {name} could not have known.")
        assert "cwd" in text or "CWD" in text, (
            f"{name} says GLOBAL without saying what it is global INSTEAD OF — "
            f"the whole bug was that the cwd decided the namespace")


def test_no_surface_still_calls_the_concurrent_refusal_a_non_fast_forward():
    """🔴 GETTING THE NAME RIGHT IS THE POINT — the next reader reasons from it.

    Measured 2026-08-26: a true concurrent create is refused by the ref
    transaction's compare-and-swap (`cannot lock ref …: reference already
    exists`), and a serialized loser by the client-side fast-forward check
    (`fetch first`). Six places called the whole thing "non-fast-forward" and
    credited the orphan root for the concurrent case, which is wrong twice.

    So every surface that explains the mechanism must now name the MEASURED
    server-side refusal — that is the load-bearing assertion here, and it is what
    the pre-change tree fails.

    The second assertion is a HEURISTIC backstop, not a proof: it only checks that
    wherever `non-fast-forward` survives, the same paragraph also carries one of
    the phrases that mark the client-side/serialized case or an explicit
    disclaimer. It can be walked by a paragraph that uses none of them; the
    positive assertion cannot.
    """
    ok_context = ("fetch first", "serial", "client", "wrong name",
                  "neither is spelled", "plays no part")
    for name, path in (("scripts/claim-work.sh", SCRIPT),
                       ("shared-queue.md", SHARED_QUEUE),
                       ("design-claim-by-push.md", DESIGN_DOC)):
        text = path.read_text(encoding="utf-8")
        assert "reference already exists" in text, (
            f"{name} explains the lock without naming the refusal the server "
            f"actually emits for two concurrent creates — the atomicity is the "
            f"ref transaction's compare-and-swap on `old=0000…`, not a "
            f"non-fast-forward, and the orphan root plays no part in it")
        for para in re.split(r"\n\s*\n", text):
            if "non-fast-forward" not in para.lower():
                continue
            low = para.lower()
            assert any(k in low for k in ok_context), (
                f"{name} still describes a rejection as non-fast-forward outside "
                f"the serialized/client-side case where that name is correct:\n"
                f"{para}")


def test_the_preflight_sweep_is_on_the_unconditional_path_not_the_degraded_one():
    """🔴 A REGRESSION THE FIRST ROUND INTRODUCED. The old `/resume` step 6
    required a `gh pr list` sweep before starting AND again before
    `gh pr create`, plus pushing the branch immediately. Landing the lock demoted
    both into the fail-open paragraph, and `shared-queue.md` said in as many words
    "for a degraded run only".

    That is wrong on the docs' own evidence: the sweep is the only thing that can
    see a duplicate that was NEVER CLAIMED, a class `design-claim-by-push.md`
    lists under "What is NOT covered". A mechanism covering something the lock
    cannot is not a fallback for it.

    🔴 Pinned as a NORMALISED WHOLE SENTENCE, not a keyword: a guard on the words
    "gh pr list" is walkable by rewording the paragraph around them into a
    fallback again, which is exactly how this regressed.
    """
    step6 = _normalised(_resume_step_6())
    assert step6.count("gh pr list") >= 2, (
        f"`/resume` step 6 names the sweep {step6.count('gh pr list')} time(s); "
        f"it is required at TWO moments — before starting and again immediately "
        f"before `gh pr create`")
    assert "(b) IS NOT A DEGRADED-RUN FALLBACK AND MUST NOT BE TREATED AS ONE." in step6, (
        "step 6 no longer states, in the imperative, that the sweep is not a "
        "fallback. Reword the surrounding prose freely — but that sentence is "
        "the machine-readable half, and without it the demotion can silently "
        "happen again.")
    assert "push the branch the moment you create it" in step6.lower(), (
        "step 6 dropped the immediate branch push, which collapses the ~20-minute "
        "invisible window to ~0")

    shared = _normalised(SHARED_QUEUE.read_text(encoding="utf-8"))
    assert "for a degraded run only" not in shared, (
        "shared-queue.md still calls the `gh pr list` sweep a degraded-run "
        "fallback. It covers a class the lock structurally cannot.")
    assert "NOT a degraded-run fallback" in shared, shared[:0] or (
        "shared-queue.md no longer says the sweep is unconditional")


# 🔴 THE WARNING, AS A WHOLE NORMALISED SENTENCE. See the test below for why a
# keyword was not enough. Deliberately free of markdown emphasis, backticks and
# line-leading bullets so ONE spelling can live in a bash comment header, a
# `usage()` heredoc and three markdown docs unchanged.
PUBLIC_SENTENCE = (
    "A claim commit is pushed to the canonical origin and this repo is PUBLIC: "
    "keep the subject generic — no client names, real hostnames, paths or "
    "captured text."
)


def _prose(text: str) -> str:
    """`_normalised`, plus line-leading comment / quote markers stripped.

    So a sentence can be pinned identically across a `#`-commented bash header, a
    heredoc and a markdown doc without the marker landing mid-sentence when the
    line wraps. Bullet characters are NOT stripped: a `-` can legitimately start
    a wrapped line of prose, and stripping it would let a reworded bullet satisfy
    a sentence pin.
    """
    return _normalised(re.sub(r"(?m)^[ \t]*[#>]+[ \t]?", "", text))


def test_every_surface_warns_that_a_claim_subject_is_PUBLIC():
    """A claim commit is pushed to the canonical origin with the `--subject` text
    verbatim, and this repo is PUBLIC. All four content gates read `git ls-files`
    and are structurally blind to a ref-only commit, so the person typing the
    subject is the only control there is — which makes the warning the control.

    🔴 PINNED AS A NORMALISED WHOLE SENTENCE, not `"PUBLIC" in text`. The keyword
    version was walkable by any surface that mentions the word for any reason —
    and three of these five discuss "this repo is PUBLIC" in other paragraphs, so
    it was satisfied without warning anybody about the SUBJECT. Its neighbour
    `test_the_preflight_sweep_is_on_the_unconditional_path_not_the_degraded_one`
    was already pinned this way for exactly this reason; this one was not.
    `claude/RULES.md`: when the artifact under test IS prose, a guard on WORDS is
    walkable by REWORDING — pin the whole normalised string.

    Reword the surrounding paragraphs freely; this sentence is the
    machine-readable half. Changing it is a deliberate edit in six places.
    """
    helped = _run("--help")
    assert helped.returncode == RC_OK, helped.stderr
    surfaces = (
        ("claim-work --help (usage)", helped.stderr + helped.stdout),
        ("scripts/claim-work.sh", SCRIPT.read_text(encoding="utf-8")),
        ("resume/SKILL.md step 6", _resume_step_6()),
        ("shared-queue.md", SHARED_QUEUE.read_text(encoding="utf-8")),
        ("design-claim-by-push.md", DESIGN_DOC.read_text(encoding="utf-8")),
    )
    for name, text in surfaces:
        assert PUBLIC_SENTENCE in _prose(text), (
            f"{name} does not carry the canonical subject-is-PUBLIC sentence "
            f"verbatim:\n\n  {PUBLIC_SENTENCE}\n\n"
            f"A `\"PUBLIC\" in text` check passed here while the surface said "
            f"nothing about the SUBJECT — reword around it, but keep it.")


def test_the_PUBLIC_sentence_pin_is_not_satisfied_by_the_keyword_alone():
    """🔴 THE NEGATIVE CONTROL ON THE GUARD ABOVE. Until it has been watched to
    reject something, "every surface carries the sentence" is a claim about five
    files that all happen to contain the word PUBLIC.

    So: a surface that mentions PUBLIC and warns about the wrong thing must FAIL
    the sentence pin, and the real sentence must pass through `_prose` from all
    three comment shapes it has to survive.

    ⚠ LABEL: base-independent by construction — it exercises `_prose` and the
    constant, never the script, so it passes at every revision. It is a control
    ON THE GUARD ABOVE, not regression cover for anything.
    """
    walker = "This repo is PUBLIC, so never commit a real media path."
    assert "PUBLIC" in walker
    assert PUBLIC_SENTENCE not in _prose(walker), (
        "the pin is satisfied by any text containing the word PUBLIC")

    for shape in (
            "# " + PUBLIC_SENTENCE,
            "# A claim commit is pushed to the canonical origin and this repo is\n"
            "# PUBLIC: keep the subject generic — no client names, real\n"
            "# hostnames, paths or captured text.",
            "> " + PUBLIC_SENTENCE,
            PUBLIC_SENTENCE.replace(": ", ":\n"),
    ):
        assert PUBLIC_SENTENCE in _prose(shape), (
            f"`_prose` cannot see the sentence in this shape, so a surface that "
            f"carries it correctly would still fail:\n{shape}")


def test_the_claim_commit_records_a_hashed_owner_id_not_an_absolute_path(tmp_path):
    """The ownership token has to distinguish two sessions; it does NOT have to
    name a directory. An absolute cwd would put a client repo's name on a public
    remote, so the commit carries a hash instead.

    ⚠ HASHED IS NOT OPAQUE, and the earlier name for this test said otherwise.
    The token it replaced was `git hash-object` over a short, guessable absolute
    path — recoverable in one command. The current one mixes in `/etc/machine-id`,
    which is not readable off-host, so it is no longer trivially recomputable;
    it is still a DISCRIMINATOR, not a secret, and `--force` bypasses the gate on
    purpose. What this test pins is the leak, not a secrecy claim.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    assert _run("topic-1", repo=a).returncode == RC_OK

    body = _git("-C", str(origin), "log", "-1", "--format=%B",
                f"{CLAIM_NS}topic-1").stdout
    assert re.search(r"^owner-id: [0-9a-f]{6,}$", body, re.M), (
        f"the claim commit does not carry a hashed owner-id:\n{body}")
    assert "cwd-id:" not in body, (
        f"the claim commit still WRITES the superseded cwd-id trailer — it is "
        f"read for old refs and must never be written again:\n{body}")
    assert str(a) not in body, (
        f"the claim commit published an ABSOLUTE PATH to the remote:\n{body}")
    assert re.search(r"^host: \S+$", body, re.M), body
    assert re.search(r"^nonce: \S+$", body, re.M), body


# ── 🔴 refs OUTLIVE a format change ──────────────────────────────────────────

def _legacy_claim(origin: Path, work: Path, slug: str, cwd: str,
                  host: str, subject: str = "legacy claim") -> str:
    """Publish a claim in the PRE-2026-08-26 format: an absolute `cwd:` trailer
    and no `cwd-id:`. Built with raw git so it is genuinely the old shape rather
    than whatever the current script emits."""
    env = _env()
    tree = _git("-C", str(work), "mktree", input="", env=env).stdout.strip()
    body = (f"claim({slug}): {subject}\n\n"
            f"claimed-by: t <t@localhost>\n"
            f"host: {host}\n"
            f"cwd: {cwd}\n"
            f"nonce: 1-2\n")
    sha = _git("-C", str(work), "commit-tree", tree, input=body, env=env).stdout.strip()
    _git("-C", str(work), "push", "origin", f"{sha}:{CLAIM_NS}{slug}", env=env)
    return sha


def test_a_claim_in_the_pre_cwd_id_format_is_still_recognised_as_its_holders_own(tmp_path):
    """🔴 FOUND BY VERIFYING LIVE, NOT BY THE SUITE. At the moment the ownership
    gate landed, THREE claims made by the pre-hash version were live on the real
    origin, each carrying an absolute `cwd:` and no `cwd-id:`. A gate that only
    reads the new field would have locked their own holder out of `--release`
    without `--force` for the rest of the TTL — the new guard turning into the
    stuck-lock it exists to prevent.

    So ownership reads the legacy field too. Measured at BOTH points, because
    "accepts the legacy field" and "accepts it from anyone" are different bugs:
    the matching cwd releases without `--force`, a different cwd is still refused.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")
    host = os.uname().nodename

    # (1) The legacy claim's `cwd:` matches THIS invocation's ident repo.
    _legacy_claim(origin, a, "legacy-mine", cwd=str(a), host=host)
    mine = _run("--release", "legacy-mine", repo=a)
    assert mine.returncode == RC_OK, (
        f"the holder of a pre-cwd-id claim could not release it without --force "
        f"(rc={mine.returncode}) — the ownership gate locks them out of their own "
        f"live claim for the rest of the TTL\n{mine.stdout}\n{mine.stderr}")
    assert f"{CLAIM_NS}legacy-mine" not in _remote_refs(origin)

    # (2) …and it is NOT everybody's. Same legacy shape, a different cwd.
    _legacy_claim(origin, a, "legacy-theirs", cwd=str(a), host=host)
    theirs = _run("--release", "legacy-theirs", repo=b)
    assert theirs.returncode == RC_TAKEN, (
        f"the legacy branch accepted a claim recorded against a DIFFERENT cwd "
        f"(rc={theirs.returncode}) — it is reading the field but not comparing it"
        f"\n{theirs.stdout}\n{theirs.stderr}")
    assert f"{CLAIM_NS}legacy-theirs" in _remote_refs(origin)

    # (3) …and a reader is told the format, not "unknown".
    shown = _run("--check", "legacy-theirs", repo=b)
    assert shown.returncode == RC_TAKEN
    assert str(a) in shown.stdout and "pre-2026-08-26 claim format" in shown.stdout, (
        f"a transitional claim's origin is reported as unknown:\n{shown.stdout}")


# ── 🔴 ROUND 3 / B1: the owner token was wrong in BOTH DIRECTIONS ─────────────
#
# The round-2 token was `(uname -n, git hash-object($PWD))`. Measured on this
# fleet 2026-08-26:
#
#   TOO LOOSE — `uname -n` is `nixos` on BOTH hosts and
#     `/home/zach/workspace/devrc` exists on both, so the two hosts computed the
#     IDENTICAL token. A laptop session was told "— THIS SESSION (you already
#     hold it)" about a WORKBENCH claim and released it at rc 0 with no --force.
#   TOO STRICT — the cwd half hashed the literal `$PWD`, so claiming from a repo
#     root and releasing from `<root>/scripts` was rc 10 "NOT yours". Same across
#     a worktree, which is this repo's MANDATED default for agent work.
#
# 🔴 AND THE SUITE COULD SEE NEITHER, which is the finding under the finding:
# `_run(repo=…)` ALWAYS passes `--repo <path>`, so every ownership test derived
# the token from a repo path — cwd-invariant by construction. Production never
# passes `--repo` (`/resume` step 6 says not to) and derived it from `$PWD`. The
# fixture pinned a token production did not use. Every test below therefore runs
# the BARE command with `cwd=`, and `_cwd_env` exists to make that hard to undo.

def test_release_from_a_SUBDIRECTORY_is_still_the_owners_own_claim(tmp_path):
    """🔴 THE STUCK LOCK THE GATE ITSELF CREATED, measured: claim from the repo
    root, `cd scripts/`, `--release` ⇒ rc 10, "NOT yours". The legitimate owner
    was locked out of their own claim for the full TTL, and `/resume` step 6 tells
    agents to run the bare command "from wherever you are".

    Three depths, because one measurement is not a general claim: the root itself,
    one level down, and two.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    deep = a / "sub" / "deeper"
    deep.mkdir(parents=True)
    env = _cwd_env(origin)

    for depth, where in (("root", a), ("one level", a / "sub"), ("two levels", deep)):
        claimed = _run("subdir-probe", "--subject", "generic item", cwd=a, env=env)
        assert claimed.returncode == RC_OK, f"{claimed.stdout}\n{claimed.stderr}"
        rel = _run("--release", "subdir-probe", cwd=where, env=env)
        assert rel.returncode == RC_OK, (
            f"the OWNER could not release its own claim from {depth} "
            f"({where}) — rc={rel.returncode}. The owner token is keyed on the "
            f"literal cwd, so a claim taken at the root is unreleasable from any "
            f"subdirectory and the item is blocked for the whole TTL.\n"
            f"{rel.stdout}\n{rel.stderr}")
        assert f"{CLAIM_NS}subdir-probe" not in _remote_refs(origin)


def test_two_worktrees_of_one_clone_are_DIFFERENT_owners(tmp_path):
    """🔴 ROUND 3'S DECISION, INVERTED — and this is the test its own design doc
    named as the one to flip if the call were reversed.

    Round 3 keyed the token off `--git-common-dir` and declared two linked
    worktrees of one clone to be ONE owner. Measured 2026-08-26: every linked
    worktree of a clone reports the SAME `--git-common-dir`, and `claim_is_mine`
    also decides `report_existing`'s exit code — so an UNRELATED sibling agent
    claiming a slug a peer already held got

        rc 12  "✅ THIS IS YOURS — carry on with it. Nothing to do."

    which `/resume` step 6 and `claude/RULES.md` both document as CARRY ON. The
    flagship guarantee delivered its exact opposite, in the fan-out shape this
    repo MANDATES for agent work.

    The token is now `--git-dir`, which is per-worktree. Both halves are asserted
    here, because "siblings are different owners" and "the owner can still work"
    are different claims and the round-2 failure was exactly a token that
    discriminated nothing and refused everybody:

      (1) the sibling cannot release the peer's live claim, and the ref survives;
      (2) the sibling CLAIMING it gets rc 10 STOP, not rc 12 CARRY ON;
      (3) the actual holder still can release it, from its own worktree AND from
          a subdirectory of it — which is round 3's real fix and must not regress.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    wt = _worktree(a, tmp_path / "a-wt")
    env = _cwd_env(origin)

    assert _run("wt-probe", "--subject", "generic item", cwd=a,
                env=env).returncode == RC_OK
    before = _remote_refs(origin)[f"{CLAIM_NS}wt-probe"]

    # (1) the destructive verb
    rel = _run("--release", "wt-probe", cwd=wt, env=env)
    assert rel.returncode == RC_TAKEN, (
        f"a session in a SIBLING WORKTREE released a live claim it does not hold "
        f"(rc={rel.returncode}). Every linked worktree of one clone shares a "
        f"git-common-dir, so keying the token on that makes 40+ agent worktrees "
        f"one owner.\n{rel.stdout}\n{rel.stderr}")
    assert _remote_refs(origin)[f"{CLAIM_NS}wt-probe"] == before

    # (2) 🔴 the verdict path — the one that actually costs a duplicate PR, and
    #     the one round 3's own note wrongly said the token did not touch.
    dup = _run("wt-probe", "--subject", "the same item, from the sibling",
               cwd=wt, env=env)
    assert dup.returncode == RC_TAKEN, (
        f"a sibling worktree claiming a peer's LIVE slug got rc={dup.returncode} "
        f"instead of {RC_TAKEN}. rc {RC_MINE} means CARRY ON.\n{dup.stdout}")
    assert "THIS IS YOURS" not in dup.stdout, dup.stdout
    assert "THIS SESSION" not in dup.stdout, dup.stdout
    assert "DO NOT start this item" in dup.stdout, dup.stdout

    # (3) POSITIVE CONTROL: the holder is not locked out — including from a
    #     subdirectory, which is the too-strict failure round 3 fixed.
    sub = a / "sub"
    sub.mkdir()
    assert _run("--release", "wt-probe", cwd=sub, env=env).returncode == RC_OK

    # …and symmetrically: a claim taken IN the worktree is that worktree's, and
    # releasable from a subdirectory of IT, but not from the main checkout.
    assert _run("wt-probe-2", cwd=wt, env=env).returncode == RC_OK
    assert _run("--release", "wt-probe-2", cwd=a, env=env).returncode == RC_TAKEN
    wtsub = wt / "sub"
    wtsub.mkdir()
    assert _run("--release", "wt-probe-2", cwd=wtsub, env=env).returncode == RC_OK


def test_a_concurrent_fanout_of_worktrees_gets_exactly_one_winner_and_no_carry_on(
        tmp_path):
    """🔴 THE SHAPE THE SUITE COULD NOT SEE, AND THAT IS THE FINDING UNDER THE
    FINDING — twice in a row now.

    `test_six_concurrent_first_movers_resolve_to_exactly_one_winner` uses
    `_session()`, i.e. six separate CLONES. Agents in this repo are never in
    separate clones: the mandated isolation is `git worktree add`, so the real
    fan-out is ONE clone with N linked worktrees, all sharing one git-common-dir
    and one git identity. Round 3's token could not tell them apart, and the
    concurrency test could not see it because its fixture had the wrong topology.

    Measured on the round-3 script with this exact fixture: 1 CLAIMED and
    N-1 × **rc 12 "carry on"**. At HEAD: 1 CLAIMED and N-1 × rc 10 STOP.

    N-1 losers is the assertion that matters, and it is stated as a SET so a
    single stray rc 12 fails rather than being averaged away. The `_worktree`
    fixture's own `checkout -b base` means the main checkout is a peer of the
    linked ones, so the winner may be any of them — the test asserts the
    PARTITION, never which member won.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    wts = [_worktree(a, tmp_path / f"a-wt{i}", branch=f"wt{i}") for i in range(5)]
    dirs = [a, *wts]
    env = _cwd_env(origin)

    procs = [
        subprocess.Popen(
            ["bash", str(SCRIPT), "fanout-item", "--subject", f"item from {i}"],
            cwd=str(d), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        for i, d in enumerate(dirs)
    ]
    results = [(p.wait(), *p.communicate()) for p in procs]
    codes = [rc for rc, _, _ in results]
    dump = "\n".join(f"--- {d} rc={rc}\n{out}\n{err}"
                     for d, (rc, out, err) in zip(dirs, results))

    assert codes.count(RC_OK) == 1, (
        f"expected exactly ONE winner across one clone + {len(wts)} linked "
        f"worktrees, got {codes}\n{dump}")
    assert codes.count(RC_MINE) == 0, (
        f"{codes.count(RC_MINE)} sibling worktree(s) were told rc {RC_MINE} — "
        f"'THIS IS YOURS — carry on with it' — about a peer's live claim. "
        f"`/resume` step 6 runs this command directly and reads {RC_MINE} as "
        f"carry on, so every one of them proceeds to do the same work.\n{dump}")
    assert set(codes) == {RC_OK, RC_TAKEN}, (
        f"a loser exited with something other than rc {RC_TAKEN}: {codes}\n{dump}")
    assert f"{CLAIM_NS}fanout-item" in _remote_refs(origin)
    # The losers must have been TOLD to stop, not merely have exited 10.
    for (rc, out, _), d in zip(results, dirs):
        if rc == RC_TAKEN:
            assert "DO NOT start this item" in out, f"{d}:\n{out}"


def test_two_different_clones_on_one_host_are_different_owners(tmp_path):
    """The other side of the boundary above, measured WITHOUT `--repo` so it
    exercises the production derivation. Same host, same git identity, two
    clones ⇒ two owners, and the refusal must hold.

    ⚠ LABEL: this is an INVARIANT GUARD, not regression cover. Measured against
    the pre-change script (`a6c60a58`) it PASSES — hashing the literal `$PWD`
    already separated two clones. It pins the property the new token must not
    lose while it fixes the two directions that were broken, and it must not be
    counted as coverage of a defect. `claude/RULES.md`: a guard pinning an
    invariant the bug never violated is an invariant guard; label it as one.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")
    env = _cwd_env(origin)

    assert _run("clone-probe", "--subject", "generic item", cwd=a,
                env=env).returncode == RC_OK
    before = _remote_refs(origin)[f"{CLAIM_NS}clone-probe"]

    refused = _run("--release", "clone-probe", cwd=b, env=env)
    assert refused.returncode == RC_TAKEN, (
        f"a session in a DIFFERENT clone released a live claim it does not hold "
        f"(rc={refused.returncode})\n{refused.stdout}\n{refused.stderr}")
    assert _remote_refs(origin)[f"{CLAIM_NS}clone-probe"] == before
    # …and the owner still can, so the gate is not simply "always refuse".
    assert _run("--release", "clone-probe", cwd=a, env=env).returncode == RC_OK


def test_two_hosts_produce_different_owner_tokens(tmp_path):
    """🔴 THE HALF THE ROUND-2 TOKEN GOT BACKWARDS, and the half a one-host suite
    cannot see by accident.

    `uname -n` is `nixos` on BOTH the workbench and the laptop — measured, not
    assumed — and `/home/zach/workspace/devrc` exists on both, so the round-2
    token was byte-identical on the two machines and each host read the other's
    claims as its own. The fix keys the host half off `/etc/machine-id`, whose
    values DO differ (measured: `d48f5d71…` vs `8d9fd8d4…`).

    Simulated here by two machine-id FILES, which is what the script reads —
    same path, same clone, same identity, so the machine-id is the ONLY variable
    and a green result cannot come from anything else.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    host_a = _machine_id(tmp_path, "machine-id-A", "a" * 32)
    host_b = _machine_id(tmp_path, "machine-id-B", "b" * 32)
    env_a = _cwd_env(origin, DEVRC_CLAIM_MACHINE_ID_FILE=str(host_a))
    env_b = _cwd_env(origin, DEVRC_CLAIM_MACHINE_ID_FILE=str(host_b))

    claimed = _run("host-probe", "--subject", "generic item", cwd=a, env=env_a)
    assert claimed.returncode == RC_OK, claimed.stderr
    token_a = re.search(r"owner-id ([0-9a-f]+)", claimed.stdout)
    assert token_a, claimed.stdout

    # (1) The tokens themselves differ. Read off the two runs' own output, so
    #     this is the script's derivation and not a re-implementation of it.
    refused = _run("--release", "host-probe", cwd=a, env=env_b)
    token_b = re.search(r"you are:\s+host \S+, owner-id ([0-9a-f]+)",
                        refused.stdout)
    assert token_b, refused.stdout
    assert token_a.group(1) != token_b.group(1), (
        f"two hosts computed the SAME owner token {token_a.group(1)} — the host "
        f"half discriminates nothing, which is exactly the state `uname -n` left "
        f"it in on a fleet where both machines are called `nixos`")

    # (2) …and it MATTERS: the other host is refused, and the ref survives.
    assert refused.returncode == RC_TAKEN, (
        f"the OTHER host released a live claim without --force "
        f"(rc={refused.returncode})\n{refused.stdout}\n{refused.stderr}")
    assert f"{CLAIM_NS}host-probe" in _remote_refs(origin), "the ref was deleted"
    assert "THIS SESSION" not in refused.stdout, (
        f"the other host was told the claim was its own:\n{refused.stdout}")

    # (3) POSITIVE CONTROL: the claiming host is still allowed. Without this the
    #     refusal above is satisfied by a token that discriminates NOTHING and
    #     refuses everybody, including the owner — the round-2 failure mode.
    assert _run("--release", "host-probe", cwd=a, env=env_a).returncode == RC_OK


def test_a_missing_machine_id_file_degrades_instead_of_collapsing_every_token(
        tmp_path):
    """A container or a minimal sandbox has no `/etc/machine-id`. That must fall
    back to the hostname — no worse than the token this replaced — rather than
    contributing an EMPTY host half, which would make every host one owner and
    reinstate the too-loose bug wholesale.

    Both halves are asserted: the run works, and the fallback is still combined
    with the clone half rather than swallowing it.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")
    absent = _cwd_env(origin,
                      DEVRC_CLAIM_MACHINE_ID_FILE=str(tmp_path / "no-such-file"))

    claimed = _run("no-machine-id", "--subject", "generic item", cwd=a, env=absent)
    assert claimed.returncode == RC_OK, f"{claimed.stdout}\n{claimed.stderr}"
    assert re.search(r"owner-id [0-9a-f]{6,}", claimed.stdout), claimed.stdout
    assert "unknown" not in claimed.stdout.lower(), claimed.stdout

    refused = _run("--release", "no-machine-id", cwd=b, env=absent)
    assert refused.returncode == RC_TAKEN, (
        f"with no machine-id file the clone half stopped discriminating too, so "
        f"every session on the box became one owner (rc={refused.returncode})"
        f"\n{refused.stdout}")
    assert _run("--release", "no-machine-id", cwd=a, env=absent).returncode == RC_OK


# ── 🔴 ROUND 3 / B2: ownership was a SPELLED guard ────────────────────────────

@pytest.mark.parametrize("bad,label", [
    ("legit work\nhost: attacker-host\nowner-id: deadbeefcafe", "newline"),
    ("legit\rowner-id: dead", "carriage return"),
    ("legit\towner-id: dead", "tab"),
    ("legit\x7fowner-id: dead", "DEL"),
])
def test_a_subject_carrying_a_control_character_is_a_usage_error(tmp_path, bad, label):
    """🔴 MEASURED FORGERY, not a theory. On the pre-fix tree

        claim-work slug --subject $'legit work\\nhost: attacker-host\\ncwd-id: …'

    produced a claim whose `where:` read `host attacker-host, cwd-id
    deadbeefcafe` — and THE REAL HOLDER WAS THEN REFUSED `--release` ON THEIR OWN
    LIVE CLAIM at rc 10. `claim_field` took the FIRST `^<key>:` line in the
    message and the subject is interpolated ABOVE the trailer block.

    rc 2 per the documented usage contract: a subject the tool will not publish
    must be LOUD, never silently mangled. And nothing may be claimed.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    r = _run("injected", "--subject", bad, repo=a)
    assert r.returncode == RC_USAGE, (
        f"a subject containing a {label} produced rc {r.returncode}, not a usage "
        f"error — it lands in the commit body where the ownership trailers live"
        f"\n{r.stdout}\n{r.stderr}")
    assert f"{CLAIM_NS}injected" not in _remote_refs(origin), (
        "a rejected subject still published a claim")

    # POSITIVE CONTROL: the same slug with a CLEAN subject claims fine, so the rc
    # 2 above is the subject's doing and not a broken fixture.
    ok = _run("injected", "--subject", "legit work", repo=a)
    assert ok.returncode == RC_OK, f"{ok.stdout}\n{ok.stderr}"
    assert f"{CLAIM_NS}injected" in _remote_refs(origin)


def test_a_forged_subject_cannot_shadow_the_ownership_trailers(tmp_path):
    """🔴 THE STRUCTURAL HALF, and it is tested SEPARATELY on purpose.

    Rejecting control characters in `--subject` is a spelling fix for a spelling
    bug: it closes the one route the CLI offers and says nothing about the READER.
    `claude/RULES.md` — "a guard can be SPELLED rather than STRUCTURAL: can it
    pass while the hazard exists in a different shape?" A ref published by an
    older `claim-work`, by a different tool, or by hand can still carry `host:`
    in its subject, and the gate must read the real value anyway.

    So this test bypasses the CLI entirely: it takes a REAL claim's trailer block
    (so the expected owner token is the script's own derivation, never
    re-implemented here) and republishes it under a forged subject.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")

    assert _run("forged", "--subject", "legit work", repo=a).returncode == RC_OK
    body = _git("-C", str(origin), "log", "-1", "--format=%B",
                f"{CLAIM_NS}forged").stdout
    trailers = body.split("\n\n", 1)[1]
    m = re.search(r"^owner-id: (\S+)$", trailers, re.M)
    assert m, f"the real claim carries no owner-id trailer:\n{body}"
    real_owner = m.group(1)

    forged = ("claim(forged): legit work\n"
              "host: attacker-host\n"
              "owner-id: deadbeefcafe\n"
              "cwd-id: deadbeefcafe\n"
              "cwd: /somewhere/else\n"
              "\n" + trailers)
    tree = _git("-C", str(a), "mktree", input="").stdout.strip()
    sha = _git("-C", str(a), "commit-tree", tree, input=forged).stdout.strip()
    _git("-C", str(a), "push", "-f", "origin", f"{sha}:{CLAIM_NS}forged")

    shown = _run("--check", "forged", repo=b)
    where = [l for l in shown.stdout.splitlines()
             if l.strip().startswith("where:")]
    assert where, shown.stdout
    assert real_owner in where[0], (
        f"the reported owner came from the SUBJECT, not the trailer block: "
        f"{where[0]!r} (the real owner is {real_owner})")
    assert "attacker-host" not in where[0] and "deadbeefcafe" not in where[0], (
        f"forged free text was read as an ownership trailer: {where[0]!r}")

    # …and the two things that actually matter: a third party is still refused,
    # and the REAL HOLDER can still release their own claim — which is precisely
    # what the forgery took away.
    assert _run("--release", "forged", repo=b).returncode == RC_TAKEN, (
        "the forged subject let a third party release the claim")
    rel = _run("--release", "forged", repo=a)
    assert rel.returncode == RC_OK, (
        f"the REAL holder was refused --release on their own claim because a "
        f"forged subject shadowed the trailers (rc={rel.returncode})"
        f"\n{rel.stdout}\n{rel.stderr}")

    # 🔴 LEG 2 — THE CASE THAT SEPARATES THE TWO CANDIDATE FIXES, and without it
    # `git interpret-trailers --parse` would be dead weight. `claim_field` also
    # takes the LAST matching line rather than the first, which alone defeats
    # anything PREPENDED (the subject is always first) — so leg 1 above stays
    # green with the structural read removed.
    #
    # Here the ref is a LEGACY one (`cwd:`, no `owner-id:`) whose SUBJECT spells
    # an `owner-id`. That forged line is then the ONLY `owner-id:` in the whole
    # message, so first-or-last makes no difference: a line scan reads it, decides
    # the claim belongs to `deadbeefcafe`, and locks the legacy holder out. The
    # structural read sees no owner-id trailer at all and falls through to the
    # legacy tier, which is correct.
    host = os.uname().nodename
    _legacy_claim(origin, a, "forged-legacy", cwd=str(a), host=host,
                  subject="legit work\nowner-id: deadbeefcafe")
    legacy = _run("--release", "forged-legacy", repo=a)
    assert legacy.returncode == RC_OK, (
        f"a forged `owner-id:` in the SUBJECT of a legacy claim locked its own "
        f"holder out (rc={legacy.returncode}) — the trailers are being read by "
        f"line scan, not as a trailer block\n{legacy.stdout}\n{legacy.stderr}")
    assert f"{CLAIM_NS}forged-legacy" not in _remote_refs(origin)


# ── 🔴 ROUND 3 / B3: `claim_is_mine` was computed, printed, not branched on ────

def test_re_claiming_your_own_item_is_its_own_rc_and_says_carry_on(tmp_path):
    """🔴 `/resume` step 6 says **rc 10 ⇒ STOP**. The round-2 code computed
    ownership, PRINTED "— THIS SESSION (you already hold it)", then ignored it
    and returned rc 10 with "DO NOT start this item. Pick another." three lines
    later — so a session re-claiming its own item after a context reset, or a
    second `/resume` over the same handoff doc, was told to abandon work it
    legitimately held. `claude/RULES.md`: a field that exists is not a guard,
    only a BRANCH on it is.

    Measured at all three points that must disagree — yours, somebody else's,
    and yours-but-stale — because a single rc 12 could be produced by a gate that
    returns 12 for everything.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")

    assert _run("mine", "--subject", "generic item", repo=a).returncode == RC_OK

    again = _run("mine", "--subject", "generic item", repo=a)
    assert again.returncode == RC_MINE, (
        f"re-claiming your OWN item returned rc {again.returncode}. rc "
        f"{RC_TAKEN} is documented as STOP, so this told a session to abandon "
        f"work it holds.\n{again.stdout}")
    assert "THIS IS YOURS" in again.stdout, again.stdout
    assert "DO NOT start this item" not in again.stdout, (
        f"the same output says you hold it AND tells you not to start it:"
        f"\n{again.stdout}")

    # --check agrees. Two verbs, because the rc vocabulary is per-tool, not
    # per-code-path.
    assert _run("--check", "mine", repo=a).returncode == RC_MINE

    # DISCRIMINATION: somebody else's live claim is still rc 10 with the STOP
    # wording. Without this the assertion above is satisfied by "always 12".
    theirs = _run("--check", "mine", repo=b)
    assert theirs.returncode == RC_TAKEN, theirs.stdout
    assert "DO NOT start this item" in theirs.stdout, theirs.stdout
    assert "THIS IS YOURS" not in theirs.stdout, theirs.stdout

    # A STALE claim OF YOUR OWN is still yours — rc 12 outranks rc 11 — and the
    # stale advisory is still printed, so nothing is hidden by the precedence.
    old = int(time.time()) - 30 * 86400
    aged = _env(GIT_AUTHOR_DATE=f"{old} +0000", GIT_COMMITTER_DATE=f"{old} +0000")
    assert _run("mine-old", repo=a, env=aged).returncode == RC_OK
    own_stale = _run("--check", "mine-old", repo=a)
    assert own_stale.returncode == RC_MINE, (
        f"a stale claim of your OWN read rc {own_stale.returncode}; it is still "
        f"yours and 'carry on' is the actionable answer\n{own_stale.stdout}")
    assert "STALE" in own_stale.stdout, (
        f"the staleness advisory was suppressed by the ownership branch:"
        f"\n{own_stale.stdout}")
    # …and somebody ELSE'S stale claim is still rc 11, so 12 is not swallowing it.
    assert _run("--check", "mine-old", repo=b).returncode == RC_TAKEN_STALE


# ── 🔴 ROUND 3 / B4: an exported GIT_DIR beats `-C` ───────────────────────────

def test_an_exported_GIT_DIR_cannot_make_the_lock_inert(tmp_path):
    """🔴 `GIT_DIR` OVERRIDES `-C`, so with one exported this script's
    `git init --bare "$WS"` and `git -C "$WS" remote add` both acted on the
    CALLER's repository. MEASURED 2026-08-26 on the round-2 tree:

        GIT_DIR=<other>/.git claim-work --check <slug>
        claim-work: DEGRADED — could not attach remote '<origin>'   → exit 0

    i.e. any agent with `GIT_DIR` exported got SILENT ZERO LOCKING out of a tool
    that reported success. The script deliberately unset `GIT_ASKPASS` /
    `SSH_ASKPASS`; the repo-pointer ledger was simply missing.

    Parametrised over the whole ledger would be slow; `GIT_DIR` is the one that
    beats `-C`, and `test_git_repo_isolation.py` pins that this script's copy of
    the ledger matches `gitenv.py`'s in BOTH directions — so the other ten cannot
    silently go missing.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    decoy = _session(tmp_path, origin, "Decoy", "decoy")

    poisoned = _env(DEVRC_CLAIM_REMOTE=str(origin), GIT_DIR=str(decoy / ".git"))
    # --strict so "degraded" is visible: the contract is exit 0 either way, which
    # is exactly what hid this.
    claimed = _run("poisoned", "--subject", "generic item", "--strict",
                   cwd=a, env=poisoned)
    assert claimed.returncode == RC_OK, (
        f"an exported GIT_DIR degraded the run (rc={claimed.returncode}) — with "
        f"--strict that is rc {RC_DEGRADED_STRICT}, and WITHOUT it the same run "
        f"reports exit 0 while locking nothing\n{claimed.stdout}\n{claimed.stderr}")
    assert "CLAIMED" in claimed.stdout, claimed.stdout
    assert f"{CLAIM_NS}poisoned" in _remote_refs(origin), (
        "the claim ref did not land on origin under an exported GIT_DIR")

    # …and the DECOY repository was not written into. That is the other half of
    # the hazard: GIT_DIR redirects where objects and refs go.
    decoy_refs = _git("-C", str(decoy), "for-each-ref", "--format=%(refname)").stdout
    assert "claim/" not in decoy_refs, (
        f"a claim ref landed in the repository GIT_DIR pointed at:\n{decoy_refs}")

    # …and the lock still LOCKS through it, which "the ref exists" does not prove.
    b = _session(tmp_path, origin, "Session B", "b")
    assert _run("poisoned", cwd=b, env=poisoned).returncode == RC_TAKEN


# ── 🔴 ROUND 3 / B6: a flag swallowed as the subject ──────────────────────────

def test_a_flag_swallowed_as_the_subject_is_a_usage_error(tmp_path):
    """`--subject --force` claimed with `what: --force` and silently DROPPED the
    `--force`, so the caller believed they had forced something. The identical
    nit was already fixed for `--slug-for`; this is the same treatment."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")

    r = _run("flagsub", "--subject", "--force", repo=a)
    assert r.returncode == RC_USAGE, (
        f"a flag swallowed as the subject produced rc {r.returncode}\n{r.stdout}")
    assert "--force" in r.stderr, r.stderr
    assert f"{CLAIM_NS}flagsub" not in _remote_refs(origin), (
        "a claim was published with a flag as its human description")

    # POSITIVE CONTROL: text that merely CONTAINS a dash is still fine.
    ok = _run("flagsub", "--subject", "re-run the gate --set all", repo=a)
    assert ok.returncode == RC_OK, f"{ok.stdout}\n{ok.stderr}"


@pytest.mark.parametrize("cfg", [
    ("trailer.separators", "=", "local"),
    ("trailer.owner-id.key", "OWNER=", "local"),
    ("trailer.separators", "=", "global"),
    ("trailer.owner-id.key", "OWNER=", "global"),
])
def test_the_callers_trailer_config_cannot_lock_the_owner_out(tmp_path, cfg):
    """🔴 THE STRUCTURAL TRAILER READ INHERITED THE CALLER'S GIT CONFIG, AND THE
    LINE SCAN IT REPLACED WAS CONFIG-IMMUNE.

    `claim_field` moved from an awk scan to `git interpret-trailers --parse` to
    close a forgery — and `interpret-trailers` was invoked with NO `-C`, i.e.
    inside whatever repository the agent was standing in. Two ordinary user
    settings, both measured 2026-08-26, make every ownership read return empty:

      * `trailer.separators = '='` — `key: value` stops being a trailer at all.
      * `trailer.owner-id.key = 'OWNER='` — the token is RENAMED on output
        (`owner-id: abc` printed as `OWNER=: abc`), which the first fix (pinning
        `trailer.separators`) does NOT cover. Two independent knobs, so two cases.

    An empty owner read fails CLOSED, which is the right direction and still a
    stuck lock: `--release` of your OWN 0-second-old claim ⇒ rc 10 "NOT yours",
    for the whole TTL. Measured end to end before the fix.

    🔴 TWO LAYERS, because the two defences are independent and neither covers
    the other. `local` writes to the caller's repo config — closed by running
    `interpret-trailers` with `-C "$WS"`, the throwaway repo. `global` points
    `GIT_CONFIG_GLOBAL` at a hostile file — which `-C` cannot displace, and which
    is closed only by neutralising the global/system layers for that one call. A
    suite with just the `local` cases scores the global mutant SURVIVED.
    """
    key, value, layer = cfg
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    env = _cwd_env(origin)
    if layer == "local":
        _git("-C", str(a), "config", key, value)
    else:
        hostile = tmp_path / "hostile.gitconfig"
        section, _, leaf = key.rpartition(".")
        head, _, sub = section.partition(".")
        hostile.write_text(
            (f'[{head} "{sub}"]\n' if sub else f"[{head}]\n") + f"\t{leaf} = {value}\n",
            encoding="utf-8")
        env = dict(env, GIT_CONFIG_GLOBAL=str(hostile))
        # The fixture must actually be hostile, or every assertion below passes
        # against a config git ignored. Read it back through git itself.
        seen = _git("config", "--file", str(hostile), "--get", key,
                    env=_env()).stdout.strip()
        assert seen == value, f"the hostile global config did not take: {seen!r}"

    claimed = _run("trailercfg", "--subject", "generic item", cwd=a, env=env)
    assert claimed.returncode == RC_OK, f"{claimed.stdout}\n{claimed.stderr}"

    rel = _run("--release", "trailercfg", cwd=a, env=env)
    assert rel.returncode == RC_OK, (
        f"with `{key} = {value}` in the caller's {layer} git config the owner "
        f"could not release their own claim (rc={rel.returncode}) — every "
        f"ownership trailer read empty, so the holder is locked out for the whole "
        f"TTL\n{rel.stdout}\n{rel.stderr}")
    assert f"{CLAIM_NS}trailercfg" not in _remote_refs(origin)

    # POSITIVE CONTROL: the read is not simply "always mine" now. A different
    # clone must still be refused with the same hostile config in place.
    b = _session(tmp_path, origin, "Same Identity", "b")
    if layer == "local":
        _git("-C", str(b), "config", key, value)
    assert _run("trailercfg2", cwd=a, env=env).returncode == RC_OK
    theirs = _run("--release", "trailercfg2", cwd=b, env=env)
    assert theirs.returncode == RC_TAKEN, (
        f"neutralising the trailer config made every claim readable as MINE "
        f"(rc={theirs.returncode})\n{theirs.stdout}")


def test_an_unreadable_git_dir_probe_SAYS_it_degraded(tmp_path):
    """🔴 A SILENT FALLBACK TO A DIFFERENT IDENTITY. When
    `rev-parse --path-format=absolute --git-dir` fails for ANY reason the token
    falls back to the ident DIRECTORY — reinstating the round-2 cwd-keyed token,
    with no warning at all. Its comment blamed only "not a git repository", which
    reads as unreachable; the same path fires on a `safe.directory` refusal
    (git >= 2.35.2, a directory owned by another uid) and on any git that renames
    the flag.

    Degrading is right for a tool whose contract is "never block a resume". Being
    QUIET about it is not: the symptom is rc 10 "NOT yours" on your own claim,
    which is indistinguishable from somebody else holding your slug.

    Driven with a `git` shim that fails ONLY that probe — a narrow mutation of the
    environment, not of the script, so the degraded path is genuinely reached.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    real_git = shutil.which("git")
    assert real_git, "no git on PATH"
    mockbin.write_exec(shim_dir / "git", f"""
for a in "$@"; do
  case "$a" in
    --git-dir)
      for b in "$@"; do
        [ "$b" = "--path-format=absolute" ] && exit 128
      done ;;
  esac
done
exec {real_git} "$@"
""")
    env = _cwd_env(origin)
    env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', os.environ.get('PATH', ''))}"

    r = _run("degraded-probe", "--subject", "generic item", cwd=a, env=env)
    assert r.returncode == RC_OK, f"{r.stdout}\n{r.stderr}"
    assert "could not read this directory's git dir" in r.stderr, (
        f"the owner token fell back to the DIRECTORY PATH and said nothing. "
        f"stderr was:\n{r.stderr}")
    assert "--force" in r.stderr, (
        f"the warning does not name the way out of the lockout it just created:\n"
        f"{r.stderr}")

    # NEGATIVE CONTROL: without the shim the same command must NOT warn, or the
    # assertion above is satisfied by a script that always prints it.
    clean = _run("degraded-probe-2", "--subject", "generic item", cwd=a,
                 env=_cwd_env(origin))
    assert clean.returncode == RC_OK, f"{clean.stdout}\n{clean.stderr}"
    assert "could not read this directory's git dir" not in clean.stderr, clean.stderr


def test_a_submodule_working_dir_is_a_different_owner_than_its_superproject(tmp_path):
    """⚠ LABEL: an INVARIANT GUARD, not regression cover — it passes on
    pre-change code, and `claudedocs/design-claim-by-push.md` refers to it by
    exactly that phrase. (It said so while this docstring never used the words,
    so a reader checking the doc's claim against the test found nothing to match;
    round 5 made the two agree.)

    ⚠ A DOCUMENTED CONSEQUENCE, not a bug — pinned because the script's own
    decision text says "any SUBDIRECTORY of the same worktree is the same owner"
    and a submodule working directory looks like a subdirectory while NOT being
    one. Its git dir is `<super>/.git/modules/<name>` (measured), so it is a
    different owner. That is correct — a submodule is a different repository —
    but it is the kind of thing a maintainer would otherwise read the wrong way
    off the decision text, and devrc has no submodules to exercise it in anger.

    `protocol.file.allow=always` because git >= 2.38.1 refuses `file://`
    submodule transport by default; the fixture is local by construction.
    """
    origin = _bare_origin(tmp_path)
    sub_origin = _bare_origin(tmp_path, "subproject.git")
    sub_src = _session(tmp_path, sub_origin, "Same Identity", "subsrc")
    _git("-C", str(sub_src), "commit", "-q", "--allow-empty", "-m", "sub base")
    _git("-C", str(sub_src), "push", "-q", "origin", "HEAD:refs/heads/main")
    # `_bare_origin` does not pass `-b`, so the bare HEAD points at git's default
    # branch name and a `submodule add` clone would find no HEAD to check out
    # (exit 128). Point it at what was actually pushed.
    _git("-C", str(sub_origin), "symbolic-ref", "HEAD", "refs/heads/main")

    a = _session(tmp_path, origin, "Same Identity", "a")
    _git("-C", str(a), "commit", "-q", "--allow-empty", "-m", "base")
    _git("-C", str(a), "-c", "protocol.file.allow=always", "submodule", "add",
         "-q", str(sub_origin), "vendor")
    _git("-C", str(a), "commit", "-q", "-m", "add submodule")
    sub_wd = a / "vendor"
    assert (sub_wd / ".git").exists()
    env = _cwd_env(origin)

    assert _run("submod-probe", "--subject", "generic item", cwd=a,
                env=env).returncode == RC_OK
    r = _run("--release", "submod-probe", cwd=sub_wd, env=env)
    assert r.returncode == RC_TAKEN, (
        f"the submodule working directory was read as the SUPERPROJECT's owner "
        f"(rc={r.returncode}). If this ever becomes desirable, change the "
        f"decision text in claim-work.sh with it.\n{r.stdout}")
    # POSITIVE CONTROL: the superproject still owns its own claim.
    assert _run("--release", "submod-probe", cwd=a, env=env).returncode == RC_OK


def test_a_legacy_cwd_claim_from_another_HOST_is_not_yours(tmp_path):
    """The legacy tier's `host:` check, which is the tier that is ACTUALLY LIVE —
    all three claims on the real origin are `cwd:`-format. It was outside the
    committed mutation sweep's closed set, so nothing had ever watched it fail.

    ⚠ LABEL: an INVARIANT GUARD, not regression cover — it passes at `9d6efc29`.
    It exists so the legacy comparison cannot be widened to "any legacy claim
    with a matching path is mine" without a test going red, and it is the killer
    for the `legacy-host-check-removed` mutant.

    ⚠ AND THE HONEST LIMIT, which is the whole reason the legacy tiers are
    transitional: `host:` is `uname -n`, which is `nixos` on BOTH machines in this
    fleet, so on the real hosts this check discriminates NOTHING. The test proves
    the branch exists and is reached; it does not prove the fleet is protected.
    That is unfixable for an already-published ref.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    env = _cwd_env(origin)

    _legacy_claim(origin, a, "legacy-host", cwd=str(a), host="some-other-host")
    r = _run("--release", "legacy-host", cwd=a, env=env)
    assert r.returncode == RC_TAKEN, (
        f"a legacy claim recorded against a DIFFERENT host was released as "
        f"'mine' (rc={r.returncode})\n{r.stdout}")
    assert f"{CLAIM_NS}legacy-host" in _remote_refs(origin)

    # POSITIVE CONTROL: the same claim with THIS host's name is releasable, so
    # the refusal above is the host check and not the path check.
    _legacy_claim(origin, a, "legacy-host-ok", cwd=str(a),
                  host=os.uname().nodename)
    assert _run("--release", "legacy-host-ok", cwd=a, env=env).returncode == RC_OK


def test_a_legacy_cwd_claim_is_releasable_from_a_worktree_of_that_clone(tmp_path):
    """🔴 THE REFS THAT ARE ACTUALLY LIVE. Measured 2026-08-26: all three claims
    on the real origin are in the oldest (`cwd:`) format, each recorded against
    `/home/zach/workspace/devrc`. Fixing the cwd-sensitivity only for NEW claims
    would leave their holder unable to release them from a worktree or a
    subdirectory for the rest of the TTL — the same stuck lock, on the refs that
    exist today rather than on hypothetical ones.

    So the legacy tier also accepts the CLONE ROOT. Measured at both points,
    because "accepts the clone root" and "accepts it from anyone" are different
    bugs: a worktree of the claiming clone releases without `--force`, a
    different clone is still refused.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    b = _session(tmp_path, origin, "Same Identity", "b")
    wt = _worktree(a, tmp_path / "a-legacy-wt")
    host = os.uname().nodename
    env = _cwd_env(origin)

    _legacy_claim(origin, a, "legacy-wt", cwd=str(a), host=host)
    mine = _run("--release", "legacy-wt", cwd=wt, env=env)
    assert mine.returncode == RC_OK, (
        f"a live legacy claim could not be released from a WORKTREE of the clone "
        f"it was taken in (rc={mine.returncode}) — the three claims on the real "
        f"origin are all in this format\n{mine.stdout}\n{mine.stderr}")
    assert f"{CLAIM_NS}legacy-wt" not in _remote_refs(origin)

    # …and the widening stops at the clone. A different clone is still refused.
    _legacy_claim(origin, a, "legacy-other", cwd=str(a), host=host)
    theirs = _run("--release", "legacy-other", cwd=b, env=env)
    assert theirs.returncode == RC_TAKEN, (
        f"the legacy clone-root accept widened to a DIFFERENT clone "
        f"(rc={theirs.returncode})\n{theirs.stdout}")
    assert f"{CLAIM_NS}legacy-other" in _remote_refs(origin)


# ── 🔴 ROUND 5 / F1: the legacy tier still told a SIBLING WORKTREE "carry on" ──
#
# Round 4 narrowed the `owner-id:` token to per-worktree and pinned it well. It
# left the LEGACY `cwd:` tier accepting the whole clone — for BOTH readers of
# `claim_is_mine`. On the refs that actually exist that is round 3's flagship bug
# verbatim, because every claim live on the real origin is legacy-format and
# recorded at `<clone>`. Measured read-only from a linked worktree of the real
# clone, 2026-08-26, before this fix:
#
#     claim/devrc-nix-read-path-dirt                       → rc 12 "carry on"
#     claim/devrc-xdist-collection-mismatch-gh-issue-guard → rc 12 "carry on"
#     claim/analyze-service-index-backup-1                 → rc 12 "carry on"
#
# 100% of them, in a clone with 61 registered worktrees, with `/resume` step 6
# running `claim-work "$SLUG"` directly and documenting rc 12 as CARRY ON.
#
# The fix is a SCOPE, not a narrowing: `claim_is_mine` takes the legacy scope as
# its second argument, `report_existing` (the verdict) omits it and gets strict
# per-worktree identity, `require_ownership_or_force` (the destructive verbs)
# passes `clone` and keeps the widening the live refs depend on.

def test_a_sibling_worktree_is_told_STOP_not_carry_on_about_a_legacy_claim(tmp_path):
    """🔴 F1. The claim/`--check` VERDICT must not accept the clone-root
    widening: "may I delete this ref?" and "should I start this work?" are
    different questions and only the first can afford the forgiving answer.

    Four points, because a gate that answered a constant would satisfy any one of
    them: the sibling's `--check`, the sibling's bare CLAIM (what `/resume`
    actually runs), the holder standing where the claim was taken, and the
    sibling's `--release` — which must still SUCCEED, since keeping the live refs
    releasable from anywhere in their clone is the entire reason the widening
    exists.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    wt = _worktree(a, tmp_path / "a-sibling-wt")
    host = os.uname().nodename
    env = _cwd_env(origin)

    _legacy_claim(origin, a, "legacy-verdict", cwd=str(a), host=host)

    # (1) THE BUG: a sibling worktree asking "should I start this?"
    sibling = _run("--check", "legacy-verdict", cwd=wt, env=env)
    assert sibling.returncode == RC_TAKEN, (
        f"a SIBLING worktree was told rc {sibling.returncode} about a legacy "
        f"claim taken in a different worktree of the same clone. rc {RC_MINE} "
        f"means CARRY ON, and every claim live on the real origin is in this "
        f"format — so this is round 3's bug on 100% of the refs that exist."
        f"\n{sibling.stdout}")
    assert "DO NOT start this item" in sibling.stdout, sibling.stdout
    assert "THIS IS YOURS" not in sibling.stdout, sibling.stdout

    # (2) …and through the bare CLAIM verb, which is the one `/resume` step 6
    # runs. Two verbs, because the rc vocabulary is per-tool, not per-code-path.
    claimed = _run("legacy-verdict", "--subject", "generic item", cwd=wt, env=env)
    assert claimed.returncode == RC_TAKEN, (
        f"the CLAIM verb told a sibling worktree rc {claimed.returncode}"
        f"\n{claimed.stdout}")
    assert "THIS IS YOURS" not in claimed.stdout, claimed.stdout

    # (3) POSITIVE CONTROL: a gate that returned rc 10 for everything would pass
    # (1) and (2). The holder, standing exactly where the claim was taken, still
    # reads rc 12 CARRY ON.
    holder = _run("--check", "legacy-verdict", cwd=a, env=env)
    assert holder.returncode == RC_MINE, (
        f"the legacy holder lost their own rc {RC_MINE} verdict "
        f"(rc={holder.returncode})\n{holder.stdout}")
    assert "THIS IS YOURS" in holder.stdout, holder.stdout

    # (4) …and the DESTRUCTIVE verb keeps the wide answer. ⚠ THIS IS THE PRICE,
    # PINNED RATHER THAN HIDDEN: the sibling worktree that was just told STOP can
    # still `--release` the claim without `--force`, because a legacy ref records
    # a bare path and nothing distinguishes a sibling from the holder standing in
    # a subdirectory. rc 10 is the safe side of the START question; a wrong YES
    # on the DELETE question costs one visible `--force`. Removing this accept
    # makes the live refs unreleasable from a worktree for the whole TTL.
    rel = _run("--release", "legacy-verdict", cwd=wt, env=env)
    assert rel.returncode == RC_OK, (
        f"the legacy clone-root accept was removed from the destructive verbs "
        f"too (rc={rel.returncode}) — the claims live on the real origin are "
        f"then stuck for the rest of the TTL\n{rel.stdout}\n{rel.stderr}")
    assert f"{CLAIM_NS}legacy-verdict" not in _remote_refs(origin)


# ── 🔴 ROUND 5 / F2: the second probe was silent too ──────────────────────────

def test_an_unreadable_git_common_dir_probe_SAYS_it_degraded(tmp_path):
    """🔴 THE SAME SILENT-FALLBACK CLASS AS
    `test_an_unreadable_git_dir_probe_SAYS_it_degraded`, reintroduced fifty lines
    below its own lesson and in the same delta that fixed it.

    `MY_CLONE_ROOT` comes from `rev-parse --path-format=absolute
    --git-common-dir`. When that probe fails the variable is EMPTY, the legacy
    clone-root accept silently stops accepting, and the holder of a legacy claim
    taken at the clone root gets rc 10 "is NOT yours" about their own lock, with
    no warning — indistinguishable from somebody else holding the slug.

    Driven with a `git` shim that fails ONLY that probe, so the degraded path is
    reached by mutating the ENVIRONMENT rather than the script. The shim leaves
    `--git-dir` working, which is what makes this a test of the SECOND probe: the
    first one's warning must not fire.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    wt = _worktree(a, tmp_path / "a-common-wt")
    host = os.uname().nodename
    shim_dir = tmp_path / "shim-common"
    shim_dir.mkdir()
    real_git = shutil.which("git")
    assert real_git, "no git on PATH"
    mockbin.write_exec(shim_dir / "git", f"""
abs=0; common=0
for a in "$@"; do
  [ "$a" = "--path-format=absolute" ] && abs=1
  [ "$a" = "--git-common-dir" ] && common=1
done
[ "$abs" = 1 ] && [ "$common" = 1 ] && exit 128
exec {real_git} "$@"
""")
    env = _cwd_env(origin)
    env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', os.environ.get('PATH', ''))}"

    _legacy_claim(origin, a, "legacy-common", cwd=str(a), host=host)
    r = _run("--release", "legacy-common", cwd=wt, env=env)
    assert r.returncode == RC_TAKEN, (
        f"the fixture did not reproduce the lockout (rc={r.returncode}); the "
        f"warning assertion below would then be vacuous\n{r.stdout}\n{r.stderr}")
    assert "could not resolve this clone's root" in r.stderr, (
        f"MY_CLONE_ROOT fell back to EMPTY and the holder was refused their own "
        f"legacy claim with no warning at all. stderr was:\n{r.stderr}")
    assert "--force" in r.stderr, (
        f"the warning does not name the way out of the lockout it just created:"
        f"\n{r.stderr}")
    # …and it is the SECOND probe's warning, not the first one's leaking over.
    assert "could not read this directory's git dir" not in r.stderr, (
        f"the git-DIR probe also failed, so this test is measuring the wrong "
        f"one:\n{r.stderr}")

    # NEGATIVE CONTROL: without the shim the same command must NOT warn — and
    # must succeed, or the assertion above is satisfied by a script that always
    # prints it.
    clean_env = _cwd_env(origin)
    _legacy_claim(origin, a, "legacy-common-2", cwd=str(a), host=host)
    clean = _run("--release", "legacy-common-2", cwd=wt, env=clean_env)
    assert clean.returncode == RC_OK, f"{clean.stdout}\n{clean.stderr}"
    assert "could not resolve this clone's root" not in clean.stderr, clean.stderr


# ── 🔴 ROUND 5 / F3: the trailer neutralisation missed the ENVIRONMENT layer ──

@pytest.mark.parametrize("envcfg", [
    pytest.param({"GIT_CONFIG_COUNT": "1",
                  "GIT_CONFIG_KEY_0": "trailer.separators",
                  "GIT_CONFIG_VALUE_0": "="},
                 id="GIT_CONFIG_COUNT-separators"),
    pytest.param({"GIT_CONFIG_COUNT": "1",
                  "GIT_CONFIG_KEY_0": "trailer.owner-id.key",
                  "GIT_CONFIG_VALUE_0": "OWNER="},
                 id="GIT_CONFIG_COUNT-key-rename"),
    pytest.param({"GIT_CONFIG_PARAMETERS": "'trailer.separators'='='"},
                 id="GIT_CONFIG_PARAMETERS-separators"),
    pytest.param({"GIT_CONFIG_PARAMETERS": "'trailer.owner-id.key'='OWNER='"},
                 id="GIT_CONFIG_PARAMETERS-key-rename"),
])
def test_the_trailer_config_in_the_ENVIRONMENT_cannot_lock_the_owner_out(
        tmp_path, envcfg):
    """🔴 THE FIFTH CONFIG LAYER. `claim_field`'s note claimed it neutralised
    `trailer.*` "AT EVERY CONFIG LAYER" while dropping four of git's five: the
    caller's repo-local (via `-C "$WS"`), global and system (via
    `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_NOSYSTEM`) — and NOT the environment.

    🔴 IT IS NOT AN EXOTIC LAYER. `GIT_CONFIG_PARAMETERS` is set BY GIT ITSELF to
    propagate a parent process's `-c` to its children, so it arrives from a hook,
    an alias or `rebase -x` with nobody typing it. Both spellings reproduce the
    full lockout on the unfixed script: the ownership read returns empty, the
    claim reports `owner-id unknown`, and `--release` of your own 0-second-old
    claim is rc 10 "NOT yours" for the whole TTL.

    Both knobs × both spellings, because `separators` and `<key>.key` are
    independent (pinning one does not cover the other — that is why the
    command-line layer's `-c trailer.separators=:` is not enough on its own).

    ⚠ LABEL, MEASURED AND NOT ASSUMED — only HALF of these cases are regression
    cover. Run against `6f55576f` (the round-4 tree):

        [GIT_CONFIG_COUNT-key-rename]        FAILED   ← the bug
        [GIT_CONFIG_PARAMETERS-key-rename]   FAILED   ← the bug
        [GIT_CONFIG_COUNT-separators]        passed   ← INVARIANT GUARD
        [GIT_CONFIG_PARAMETERS-separators]   passed   ← INVARIANT GUARD

    The two `separators` cases were ALREADY covered before the environment layer
    was dropped, because the command-line `-c trailer.separators=:` outranks
    every config layer including the environment. They are kept as invariant
    guards — they stop the pin and the unset from being removed together and
    scored as one change — but they are NOT evidence that this fix did anything.
    The `<key>.key` pair is.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    env = _cwd_env(origin, **envcfg)

    # The fixture must actually be hostile, or every assertion below passes
    # against an environment git ignored. Read it back through git itself.
    probe = subprocess.run(
        ["git", "config", "--get",
         envcfg.get("GIT_CONFIG_KEY_0", "trailer.separators")
         if "GIT_CONFIG_COUNT" in envcfg
         else envcfg["GIT_CONFIG_PARAMETERS"].split("'")[1]],
        capture_output=True, text=True, env=dict(env, GIT_CONFIG_GLOBAL="/dev/null"),
        cwd=str(a))
    assert probe.stdout.strip() == (
        envcfg.get("GIT_CONFIG_VALUE_0")
        or envcfg["GIT_CONFIG_PARAMETERS"].split("=", 1)[1].strip("'")), (
        f"the hostile environment config did not take: {probe.stdout!r}")

    claimed = _run("envtrailer", "--subject", "generic item", cwd=a, env=env)
    assert claimed.returncode == RC_OK, f"{claimed.stdout}\n{claimed.stderr}"

    rel = _run("--release", "envtrailer", cwd=a, env=env)
    assert rel.returncode == RC_OK, (
        f"with hostile `trailer.*` config in the ENVIRONMENT the owner could not "
        f"release their own claim (rc={rel.returncode}) — every ownership "
        f"trailer read empty, so the holder is locked out for the whole TTL"
        f"\n{rel.stdout}\n{rel.stderr}")
    assert f"{CLAIM_NS}envtrailer" not in _remote_refs(origin)

    # POSITIVE CONTROL: the read is not simply "always mine" now. A different
    # clone must still be refused with the same hostile environment in place.
    b = _session(tmp_path, origin, "Same Identity", "b")
    assert _run("envtrailer2", cwd=a, env=env).returncode == RC_OK
    theirs = _run("--release", "envtrailer2", cwd=b, env=env)
    assert theirs.returncode == RC_TAKEN, (
        f"neutralising the environment layer made every claim readable as MINE "
        f"(rc={theirs.returncode})\n{theirs.stdout}")


# ── 🔴 ROUND 5 / F6: the `-c trailer.separators=:` pin is NOT redundant ───────

def test_a_hostile_init_template_cannot_lock_the_owner_out(tmp_path):
    """🔴 THE ONE `trailer.*` LAYER `-C "$WS"` CANNOT DROP: `$WS`'s OWN
    repo-local config. The script neutralises the caller's local, global, system
    and environment layers for the trailer read — but `$WS` is created by
    `git init --bare "$WS"` while the caller's global config is STILL in effect,
    and `init.templateDir` copies a template's `config` straight into the new
    repository. Measured 2026-08-26: a template whose `config` sets
    `trailer.separators = "="` lands that key in `$WS/config`, where `-C "$WS"`
    puts it right back into the stack it was supposed to remove.

    So the command-line `-c trailer.separators=:` is load-bearing, not the
    "explicit positive pin" of a value we would otherwise inherit from git's
    default — an audit measured that removing it left the whole suite green,
    which is what this test exists to correct.

    ⚠ LABEL: an INVARIANT GUARD, not regression cover — it PASSES at `6f55576f`,
    measured. Nothing was broken here; the pin was simply unpinned, so an audit
    could delete it and watch 95/95 stay green. This is the test that makes the
    `separators-pin-removed` mutant killable, and that is its whole job.

    ⚠ SCOPE: this closes `separators` only. `trailer.<key>.key` planted the same
    way is a DOCUMENTED RESIDUAL, named in `claim_field`'s note along with its
    one-token fix (`git init --bare --template=`). Do not read this test as
    covering the layer.
    """
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Same Identity", "a")
    tpl = tmp_path / "hostile-template"
    tpl.mkdir()
    (tpl / "config").write_text('[trailer]\n\tseparators = "="\n', encoding="utf-8")
    hostile = tmp_path / "hostile-init.gitconfig"
    hostile.write_text(f'[init]\n\ttemplateDir = {tpl}\n', encoding="utf-8")
    env = _cwd_env(origin, GIT_CONFIG_GLOBAL=str(hostile))

    # The fixture must actually plant the key, or the assertions below pass
    # against a template git ignored. Build a bare repo the same way the script
    # does and read the result back through git.
    probe_repo = tmp_path / "probe.git"
    _git("init", "-q", "--bare", str(probe_repo), env=env)
    planted = _git("-C", str(probe_repo), "config", "--get", "trailer.separators",
                   env=_env(), check=False).stdout.strip()
    assert planted == "=", (
        f"the hostile init template did not reach the new repo's config "
        f"({planted!r}) — this test would then be vacuous")

    claimed = _run("tpltrailer", "--subject", "generic item", cwd=a, env=env)
    assert claimed.returncode == RC_OK, f"{claimed.stdout}\n{claimed.stderr}"

    rel = _run("--release", "tpltrailer", cwd=a, env=env)
    assert rel.returncode == RC_OK, (
        f"a `trailer.separators` planted into the SCRATCH repo's own config by "
        f"`init.templateDir` locked the owner out of their own claim "
        f"(rc={rel.returncode})\n{rel.stdout}\n{rel.stderr}")
    assert f"{CLAIM_NS}tpltrailer" not in _remote_refs(origin)

    # POSITIVE CONTROL: still not "always mine". A different clone is refused.
    b = _session(tmp_path, origin, "Same Identity", "b")
    assert _run("tpltrailer2", cwd=a, env=env).returncode == RC_OK
    assert _run("--release", "tpltrailer2", cwd=b,
                env=env).returncode == RC_TAKEN
