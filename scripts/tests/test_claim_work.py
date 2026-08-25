"""`scripts/claim-work.sh` — the claim-by-push lock on a shared next-step queue.

WHAT IS UNDER TEST, AND WHAT DELIBERATELY IS NOT
------------------------------------------------
The absence of duplicate work is UNOBSERVABLE — you cannot write a test that
asserts two sessions did not build the same thing. So this file exercises the
MECHANISM instead, and the mechanism is one claim:

    publishing an ORPHAN commit to `refs/heads/claim/<slug>` on origin is an
    ATOMIC compare-and-swap performed by the receiving git, not a check we take
    and then act on.

Every case below either exercises that claim or attacks it:

  * `test_the_lock_is_gits_own_ref_compare_and_swap_not_a_check_then_act`
    verifies the atomicity EMPIRICALLY against a real git server process — with
    raw git first (so the property is pinned independently of our script) and
    then with six genuinely concurrent invocations of the script.

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
"""
from __future__ import annotations

import hashlib
import os
import re
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
LOCK_PUSH = 'push -q origin "${sha}:${CLAIM_NS}${SLUG}" 2>"$push_err"'
MUTANT_PUSH = 'push -q --force origin "${sha}:${CLAIM_NS}${SLUG}" 2>"$push_err"'


# ── fixtures ──────────────────────────────────────────────────────────────────

def _env(**overrides: str) -> dict:
    """Hermetic git env. `GIT_CONFIG_GLOBAL=/dev/null` is part of it, so the
    identity a claim carries can only come from the fixture repo's LOCAL config —
    which is exactly the path the script uses in production."""
    return hermetic_git.hermetic_git_env(**overrides)


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
         env: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke the script through `bash <path>`, never through its shebang.

    `/usr/bin/env` does not exist in the nix build sandbox and this repo has a
    repo-wide guard about it; `bash <path>` sidesteps the interpreter lookup
    entirely, which is also how `test_ship_converge.py` drives `ship.sh`.
    """
    argv = ["bash", str(script or SCRIPT)]
    if repo is not None:
        argv += ["--repo", str(repo)]
    argv += list(args)
    return subprocess.run(argv, capture_output=True, text=True, env=env or _env())


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

def test_the_lock_is_gits_own_ref_compare_and_swap_not_a_check_then_act(tmp_path):
    """Two DISTINCT orphan commits, pushed to one ref by raw git.

    Pinned with raw git rather than through the script so the property belongs
    to git — if a future git ever stopped rejecting this, the failure would name
    the mechanism instead of looking like a bug in our shell.
    """
    origin = _bare_origin(tmp_path)
    work = _session(tmp_path, origin, "Session A", "w")
    env = _env()

    def orphan(msg: str) -> str:
        tree = _git("-C", str(work), "mktree", input="", env=env).stdout.strip()
        return _git("-C", str(work), "commit-tree", tree,
                    input=msg, env=env).stdout.strip()

    one, two = orphan("claim one"), orphan("claim two")
    assert one != two, "the two claim commits must differ or this proves nothing"

    ref = f"{CLAIM_NS}race"
    ok = _git("-C", str(work), "push", "origin", f"{one}:{ref}", check=False)
    assert ok.returncode == 0, f"the first push must succeed:\n{ok.stderr}"

    bad = _git("-C", str(work), "push", "origin", f"{two}:{ref}", check=False)
    assert bad.returncode != 0, (
        "git ACCEPTED a second unrelated orphan onto an existing claim ref — "
        "the whole mechanism rests on it refusing. Everything else in this file "
        "is downstream of this assertion."
    )
    blob = (bad.stderr + bad.stdout).lower()
    assert "non-fast-forward" in blob or "fetch first" in blob, (
        f"rejected, but not as a non-fast-forward — the rejection may be for an "
        f"unrelated reason:\n{bad.stderr}")
    assert _remote_refs(origin)[ref] == one, "the loser's commit overwrote the winner's"


def test_six_concurrent_first_movers_resolve_to_exactly_one_winner(tmp_path):
    """🔴 THE FIRST-MOVER CASE, WHICH IS THE ONE A PRE-FLIGHT CHECK CANNOT COVER.

    Six sessions start at once; none can see any of the others, because at the
    moment each decides to start, none of the others has produced anything to
    see. A check-then-act design has a window here and this has none: the winner
    is decided by the server's ref transaction.
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

@pytest.mark.parametrize("bad", ["BAD SLUG", "../escape", "-leading-dash",
                                 "has/slash", "trailing.", "with..dots"])
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
