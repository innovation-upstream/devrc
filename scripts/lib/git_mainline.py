#!/usr/bin/env python3
"""What is THIS repo's mainline ref? Derived from git, never guessed. READ-ONLY.

🔴 WHY THIS IS NOT A LIST OF NAMES. It used to be, in `subsystem_touch.py`:

    BASE_REF_CANDIDATES = ("origin/main", "origin/master", "main", "master")

— a ladder that had already been extended once, reactively, the first time a
repo used `master`. On 2026-08-21 the next repo arrived: `homelab-infra`, whose
mainline is `trunk`. Every consumer of that ladder returned `no-base-ref` there,
and the one that mattered — the `--commit` window escalation — was inert in
exactly the repo it had been called for. Appending `"trunk"` would have bought
until the repo after that. So the ladder is now a FALLBACK behind a derivation:
git already knows the answer, and `git symbolic-ref refs/remotes/origin/HEAD`
reads it out of the local ref store with NO network call, which is the posture
every consumer here requires.

🔴 THE DERIVED REF IS A CLAIM, NOT AN ANSWER — VALIDATE IT, ALWAYS. `refs/remotes/
origin/HEAD` is an ordinary symref and it can point at a ref that DOES NOT EXIST;
`git symbolic-ref` still prints the target, cheerfully, exit 0. This is not
hypothetical and not rare: measured 2026-08-21 in this very repo, `devrc`'s
`refs/remotes/origin/HEAD` pointed at `refs/remotes/origin/trunk` — a ref with no
object behind it, left by a concurrent agent's fixture — while devrc's actual
mainline is `main`. Trusting the symref there would have inverted the bug this
module exists to fix, deriving `trunk` for a `main` repo. So every candidate,
derived or fallback, goes through the SAME `rev-parse --verify` existence check
and the first that RESOLVES wins. A dangling symref costs one failed rev-parse
and then the ladder proceeds, which is why devrc still answers `origin/main`.

🔴 A FAILURE STAYS NAMEABLE. `resolve_base_ref` returns the ref AND the full
ladder it tried, so a caller that finds nothing can say *what* it looked for
rather than emitting a bare empty result. `claude/RULES.md`: an empty result
cannot distinguish two mechanisms.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "FALLBACK_BASE_REFS",
    "ORIGIN_HEAD_REF",
    "origin_head_ref",
    "base_ref_ladder",
    "resolve_base_ref",
    "commits_behind",
]

#: Tried only AFTER the derived ref, and only because a clone can legitimately
#: have no `origin/HEAD` (never `git remote set-head`, or no remote at all).
#: Remote-tracking refs come first so that on a branch named `main` with unpushed
#: local commits the window is those commits — the diverged-host case devrc's
#: CLAUDE.md describes — not empty.
FALLBACK_BASE_REFS: tuple[str, ...] = ("origin/main", "origin/master", "main", "master")

#: The symref git writes on clone (and on `git remote set-head`). Named rather
#: than inlined so the one place that reads it is greppable.
ORIGIN_HEAD_REF = "refs/remotes/origin/HEAD"


def _git(repo: str | Path, args: Sequence[str]) -> str | None:
    """stdout, or None if git refused. Never raises, never takes the index lock.

    `GIT_OPTIONAL_LOCKS=0` for the same reason `subsystem_touch._git` sets it: a
    concurrent agent in the same checkout is the normal case in these repos, and
    a helper that can block someone else's commit is not read-only in the way
    that matters.
    """
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            env=env,
        )
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def origin_head_ref(repo: str | Path) -> str | None:
    """origin's own default branch as `origin/<name>`, UNVALIDATED, or None.

    🔴 The return value is what the symref SAYS, not a ref that is known to
    exist — see the module docstring's measured `devrc` case. Callers must
    resolve it; `base_ref_ladder`/`resolve_base_ref` do.
    """
    out = _git(repo, ["symbolic-ref", "--quiet", "--short", ORIGIN_HEAD_REF])
    if out is None:
        return None
    ref = out.strip()
    return ref or None


def base_ref_ladder(
    repo: str | Path,
    *,
    fallback: Iterable[str] = FALLBACK_BASE_REFS,
) -> tuple[str, ...]:
    """Every ref to try, best first, de-duplicated in order. Runs one git command.

    Exactly ONE derived rung — the remote-tracking ref the symref names — and
    then `fallback`.

    🔴 THE LOCAL COUNTERPART IS DELIBERATELY NOT A RUNG, and it was, until it was
    measured. Offering `X` after `origin/X` looks like the same remote-then-local
    shape the fallback ladder has, but it turns a dangling `origin/HEAD` from a
    harmless miss into a WRONG ANSWER: in `devrc` on 2026-08-21 the symref
    pointed at a non-existent `origin/trunk` **and** a stray local `trunk` branch
    existed beside `main` (both left by a concurrent agent's fixture), so the
    local rung selected `trunk` and the commit window came back with 11 commits
    off an unrelated branch — a plausible number, silently wrong, where the
    ladder it replaced had been right. A symref that does not resolve is evidence
    that the symref is wrong, not evidence about a same-named local branch. So
    the derivation is trusted whole or not at all, and a dangling one costs one
    failed `rev-parse` before `fallback` takes over.
    """
    ladder: list[str] = []
    derived = origin_head_ref(repo)
    if derived:
        ladder.append(derived)
    for cand in fallback:
        if cand:
            ladder.append(cand)
    seen: list[str] = []
    for ref in ladder:
        if ref not in seen:
            seen.append(ref)
    return tuple(seen)


def resolve_base_ref(
    repo: str | Path,
    *,
    fallback: Iterable[str] = FALLBACK_BASE_REFS,
) -> tuple[str | None, tuple[str, ...]]:
    """`(the first ref that RESOLVES, the whole ladder that was tried)`.

    The ladder is returned on success as well as failure so a caller's message
    can name what it looked for. `None` means every rung was checked and none
    exists — a measured absence, not an unasked question.
    """
    ladder = base_ref_ladder(repo, fallback=fallback)
    for cand in ladder:
        if _git(repo, ["rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"]):
            return cand, ladder
    return None, ladder


def commits_behind(
    repo: str | Path,
    base_ref: str,
    *,
    path: str | None = None,
) -> int | None:
    """Commits `base_ref` has that HEAD does not — optionally only those touching
    `path`. `None` when the count could NOT be taken, which is never a 0.

    No fetch, by design: every consumer of this module is read-only and must not
    reach the network. The count is therefore a FLOOR against the refs this
    clone has already fetched — a clone that has never fetched can be far more
    stale than this returns, and never less.
    """
    args = ["rev-list", "--count", f"HEAD..{base_ref}"]
    if path is not None:
        args += ["--", path]
    out = _git(repo, args)
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None
