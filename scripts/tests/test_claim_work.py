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

from testlib import hermetic_git  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "claim-work.sh"

# The rc vocabulary the script documents at the top of itself. Spelled here as
# literals ON PURPOSE: `claude/RULES.md` — "never derive a test's expectation
# from the implementation it tests".
RC_OK = 0
RC_USAGE = 2
RC_TAKEN = 10
RC_TAKEN_STALE = 11
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
    assert _run("--check", "topic-4", repo=b).returncode == RC_TAKEN
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
    assert _run("--check", "topic-1", repo=a).returncode == RC_TAKEN
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
    for rc in (RC_USAGE, RC_TAKEN, RC_TAKEN_STALE, RC_DEGRADED_STRICT):
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
    wrapper = tmp_path / "receive-pack-then-fail.sh"
    wrapper.write_text('#!/usr/bin/env bash\ngit receive-pack "$1"\nexit 1\n')
    wrapper.chmod(0o755)

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
    assert re.search(r"where:\s+host \S+, cwd-id [0-9a-f]{6,}", second.stdout), (
        f"the refusal does not say WHERE the holder is, so with one shared git "
        f"identity it names nothing the reader can act on:\n{second.stdout}")

    # …and the SAME session is told it already holds it, which is the usability
    # half: without it, "who: Same Identity" is indistinguishable from your own.
    own = _run("--check", "topic-1", repo=a)
    assert own.returncode == RC_TAKEN
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
    assert second.returncode == RC_TAKEN, (
        f"a second invocation with every input pinned identical was told it had "
        f"CLAIMED the item (rc={second.returncode}). The two claim commits share "
        f"a sha, so the push was 'Everything up-to-date' — the nonce is not "
        f"making them differ.\n{second.stdout}\n{second.stderr}"
    )


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
    hook = hooks / "pre-push"
    hook.write_text(
        "#!/usr/bin/env bash\n"
        f'echo fired >> "{fired}"\n'
        'echo "global pre-push says no" >&2\n'
        "exit 1\n"
    )
    hook.chmod(0o755)

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
    wrapper = tmp_path / "receive-pack-recording-env.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f'env > "{dump}"\n'
        'exec git receive-pack "$1"\n'
    )
    wrapper.chmod(0o755)

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


def test_every_surface_warns_that_a_claim_subject_is_PUBLIC():
    """A claim commit is pushed to the canonical origin with the `--subject` text
    verbatim, and this repo is PUBLIC. All four content gates read `git ls-files`
    and are structurally blind to a ref-only commit, so the person typing the
    subject is the only control there is — which makes the warning the control."""
    helped = _run("--help")
    for name, text in (
            ("claim-work --help (usage)", helped.stderr + helped.stdout),
            ("scripts/claim-work.sh", SCRIPT.read_text(encoding="utf-8")),
            ("resume/SKILL.md step 6", _resume_step_6()),
            ("shared-queue.md", SHARED_QUEUE.read_text(encoding="utf-8")),
            ("design-claim-by-push.md", DESIGN_DOC.read_text(encoding="utf-8"))):
        assert "PUBLIC" in text, (
            f"{name} does not warn that what a claim publishes is public")


def test_the_claim_commit_records_an_opaque_cwd_id_not_an_absolute_path(tmp_path):
    """The ownership token has to distinguish two sessions; it does NOT have to
    name a directory. An absolute cwd would put a client repo's name on a public
    remote, so the commit carries a hash instead."""
    origin = _bare_origin(tmp_path)
    a = _session(tmp_path, origin, "Session A", "a")
    assert _run("topic-1", repo=a).returncode == RC_OK

    body = _git("-C", str(origin), "log", "-1", "--format=%B",
                f"{CLAIM_NS}topic-1").stdout
    assert re.search(r"^cwd-id: [0-9a-f]{6,}$", body, re.M), (
        f"the claim commit does not carry an opaque cwd-id:\n{body}")
    assert str(a) not in body, (
        f"the claim commit published an ABSOLUTE PATH to the remote:\n{body}")
    assert re.search(r"^host: \S+$", body, re.M), body
    assert re.search(r"^nonce: \S+$", body, re.M), body
