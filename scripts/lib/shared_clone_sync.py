#!/usr/bin/env python3
"""Fast-forward a chronically-stale SHARED CLONE — and say, machine-readably,
WHY it could not when it could not.

WHY THIS EXISTS
---------------
A 14-day audit of 443 Claude sessions found the shared base clones on this fleet
are chronically stale AND chronically dirty, and that every session re-derives
that fact from scratch (up to 78 mentions in a single transcript). Measured
drift at the time of the audit: 606 commits behind, 238, 213, 161, 83; one clone
carried 43 foreign worktrees. The clones are write-only — all work happens in
throwaway worktrees and PRs — so nothing ever advances the base clone's own
branch, and its working tree is what agents READ their instructions from.

🔴 THE CONSTRAINT THAT SHAPES EVERYTHING HERE: A NAIVE CRON ACCOMPLISHES NOTHING
--------------------------------------------------------------------------------
`git merge --ff-only` cannot be trusted to run unattended over these clones,
because the interesting outcomes are indistinguishable from the outside:

  * a clone that fast-forwarded 606 commits,
  * a clone that was already current,
  * a clone that refused because the tree is dirty,
  * a clone that refused because it has local commits upstream does not have,

all end a `git merge --ff-only || true` line with nothing on stdout worth
reading, and a bare `git merge --ff-only` in a cron exits 0 on the no-op. That
is `claude/RULES.md` → "An EMPTY RESULT cannot distinguish two mechanisms": the
observable that the most causes share identifies none of them. So this module's
product is not the merge — it is the CLASSIFICATION. Every run of every repo
ends in exactly one `STATUS`, each with its own exit code and its own sentinel
phrase, so a caller (or a test) can branch on WHICH outcome happened rather than
on whether something happened.

🔴 IT NEVER RESOLVES DIRT. NOT BY STASH, NOT BY RESET, NOT BY DISCARD.
-----------------------------------------------------------------------
`git stash` in these repos is repo-GLOBAL (`refs/stash` lives in the common git
dir), so a stash here can be popped by a concurrent agent in another worktree —
this fleet has already lost work that way. `reset --hard` and `checkout --` are
worse. So the dirty case is REPORTED, with the offending paths named (capped),
and a human decides. There is no flag to make it do otherwise, on purpose.

🔴 "DIRTY" IS AN OVERLAP QUESTION, NOT A BOOLEAN — AND THE STRICT READING WOULD
HAVE MADE THIS TOOL INERT
--------------------------------------------------------------------------------
The obvious design refuses on ANY dirt. Measured against the actual fleet, that
design never syncs anything: the sample clone this was written against carries a
modified `flake.nix` plus five untracked paths (`__pycache__/`, a scratch
`go.mod`, notes) and has carried them for weeks. A helper that always refuses is
`claude/RULES.md` → a permanently-red gate: it trains everyone to ignore it.

Git itself is not that strict either — a fast-forward touches only the paths that
CHANGED between HEAD and the upstream tip, and refuses only when one of those
would clobber a local modification. So the guard here asks git the same question
git asks:

    blocking = {paths dirty in the working tree} ∩ {paths the ff would change}

Non-blocking dirt does NOT refuse; it is reported as ADVISORY so the human still
sees it. Blocking dirt refuses with the intersecting paths named.

🔴 AND GIT IS STILL THE FINAL AUTHORITY. If the precheck says "clear" and `git
merge --ff-only` refuses anyway, that is its OWN status (`refused-ff-failed`),
never folded into `refused-dirty` and never reported as a sync. A precheck that
silently disagrees with the tool it is predicting is exactly the bug this status
exists to surface; mapping it onto a neighbouring status would hide it.

EVERY FAILURE MODE CARRIES A DISTINCT SENTINEL PHRASE
------------------------------------------------------
so a caller — or a mutation test — can tell WHICH guard fired rather than merely
that one did (the convention `scripts/lib/subsystem_touch.py` documents):

    "fast-forwarded"                  STATUS_SYNCED             exit 0
    "already current"                 STATUS_CURRENT            exit 0
    "working tree is dirty"           STATUS_REFUSED_DIRTY      exit 3
    "local commits are not upstream"  STATUS_REFUSED_DIVERGED   exit 4
    "no upstream to sync from"        STATUS_REFUSED_NO_UPSTREAM exit 5
    "HEAD is detached"                STATUS_REFUSED_DETACHED   exit 6
    "not a git repository"            STATUS_REFUSED_NOT_A_REPO exit 7
    "fetch failed"                    STATUS_REFUSED_FETCH_FAILED exit 8
    "ff refused by git"               STATUS_REFUSED_FF_FAILED  exit 9

`synced` and `current` BOTH exit 0 and are deliberately NOT collapsed: "I moved
you 606 commits" and "there was nothing to do" are the two readings a stale-clone
report exists to separate, and a caller that cannot tell them apart learns
nothing from a green. They differ in `status`, in `sentinel`, and in `moved`.

🔴 WHY THIS DOES NOT REUSE `lib/git_mainline.resolve_base_ref`
---------------------------------------------------------------
That module answers "what is this repo's MAINLINE?" — a different question, and
using it here would be actively wrong. The ff target must be the CHECKED-OUT
BRANCH'S OWN upstream: a clone parked on a feature branch must not be
fast-forwarded onto `origin/main`, which is not a fast-forward of its branch at
all and, where it happens to be one, silently abandons the branch's identity. A
branch with no upstream is a REFUSAL here (`refused-no-upstream`), never a guess.

READ-ONLY EXCEPT FOR THE FF ITSELF
-----------------------------------
The only writes are `git fetch` (into the remote-tracking refs) and, in the
`synced` case, the fast-forward. `GIT_OPTIONAL_LOCKS=0` is set on every read for
the reason `git_mainline._git` sets it: a concurrent agent in the same checkout
is the normal case in these repos, and a helper that can block someone else's
commit is not read-only in the way that matters.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "STATUS_SYNCED",
    "STATUS_CURRENT",
    "STATUS_REFUSED_DIRTY",
    "STATUS_REFUSED_DIVERGED",
    "STATUS_REFUSED_NO_UPSTREAM",
    "STATUS_REFUSED_DETACHED",
    "STATUS_REFUSED_NOT_A_REPO",
    "STATUS_REFUSED_FETCH_FAILED",
    "STATUS_REFUSED_FF_FAILED",
    "ALL_STATUSES",
    "SENTINELS",
    "EXIT_CODES",
    "DIRTY_PATH_CAP",
    "CLONE_ROOTS_ENV",
    "DEFAULT_CLONE_ROOTS",
    "SyncResult",
    "EmptyRootsError",
    "discover_clones",
    "sync_repo",
    "sync_many",
    "worst_exit_code",
]

# --------------------------------------------------------------------------- #
# The status ledger. Three tables, pinned two-way against each other by
# scripts/tests/test_shared_clone_sync.py — a status with no exit code would
# crash a caller, and an exit code naming a status nothing can emit is a
# declaration with no code path behind it (`claude/RULES.md`: a count of
# DECLARATIONS is not a count of INSTANCES).
# --------------------------------------------------------------------------- #

STATUS_SYNCED = "synced"
STATUS_CURRENT = "current"
STATUS_REFUSED_DIRTY = "refused-dirty"
STATUS_REFUSED_DIVERGED = "refused-diverged"
STATUS_REFUSED_NO_UPSTREAM = "refused-no-upstream"
STATUS_REFUSED_DETACHED = "refused-detached"
STATUS_REFUSED_NOT_A_REPO = "refused-not-a-repo"
STATUS_REFUSED_FETCH_FAILED = "refused-fetch-failed"
STATUS_REFUSED_FF_FAILED = "refused-ff-failed"

#: Declaration order is report order; it is not otherwise load-bearing.
ALL_STATUSES: tuple[str, ...] = (
    STATUS_SYNCED,
    STATUS_CURRENT,
    STATUS_REFUSED_DIRTY,
    STATUS_REFUSED_DIVERGED,
    STATUS_REFUSED_NO_UPSTREAM,
    STATUS_REFUSED_DETACHED,
    STATUS_REFUSED_NOT_A_REPO,
    STATUS_REFUSED_FETCH_FAILED,
    STATUS_REFUSED_FF_FAILED,
)

#: 🔴 PAIRWISE DISTINCT, and asserted to be. A sentinel that another status could
#: also emit tells a test that A guard fired, not WHICH — which is the whole
#: value being bought here.
SENTINELS: dict[str, str] = {
    STATUS_SYNCED: "fast-forwarded",
    STATUS_CURRENT: "already current",
    STATUS_REFUSED_DIRTY: "working tree is dirty",
    STATUS_REFUSED_DIVERGED: "local commits are not upstream",
    STATUS_REFUSED_NO_UPSTREAM: "no upstream to sync from",
    STATUS_REFUSED_DETACHED: "HEAD is detached",
    STATUS_REFUSED_NOT_A_REPO: "not a git repository",
    STATUS_REFUSED_FETCH_FAILED: "fetch failed",
    STATUS_REFUSED_FF_FAILED: "ff refused by git",
}

#: 🔴 Distinct non-zero codes so a shell caller can branch WITHOUT parsing text.
#: 1 and 2 are deliberately left to the CLI (1 = unexpected, 2 = usage / nothing
#: to check), so a repo-level verdict can never be confused with a broken call.
EXIT_CODES: dict[str, int] = {
    STATUS_SYNCED: 0,
    STATUS_CURRENT: 0,
    STATUS_REFUSED_DIRTY: 3,
    STATUS_REFUSED_DIVERGED: 4,
    STATUS_REFUSED_NO_UPSTREAM: 5,
    STATUS_REFUSED_DETACHED: 6,
    STATUS_REFUSED_NOT_A_REPO: 7,
    STATUS_REFUSED_FETCH_FAILED: 8,
    STATUS_REFUSED_FF_FAILED: 9,
}

#: How many dirty paths to NAME before summarising the rest. A refusal a human
#: cannot act on is not much better than a silent one, so the list is capped
#: rather than dropped, and the full count is always reported beside it.
DIRTY_PATH_CAP = 10

#: Colon-separated roots to scan when no repo arguments are given.
CLONE_ROOTS_ENV = "DEVRC_CLONE_ROOTS"

#: 🔴 A DEFAULT, NEVER A SILENT ONE. `sync_many` records the roots it scanned and
#: the clones it found in `SyncRun.defaulted_from`, and the CLI prints both
#: before it touches anything — the operator must be able to see which repos a
#: no-argument invocation decided to act on.
DEFAULT_CLONE_ROOTS: tuple[str, ...] = ("~/workspace",)

#: Bound the network leg. A hung fetch must not wedge an unattended run; it must
#: land in `refused-fetch-failed`, which is a reading, not a hang.
FETCH_TIMEOUT_SECS = 120


class EmptyRootsError(ValueError):
    """`DEVRC_CLONE_ROOTS` was SET but EMPTY.

    🔴 `os.environ.get(VAR) or DEFAULT` cannot tell UNSET from SET-BUT-EMPTY, and
    here the fallback is the operator's ENTIRE workspace. `scripts/tests/
    test_repo_path_defaults.py` documents the same shape biting `ship.sh`,
    `drift-check.sh` and the index's `commit.sh`: a caller whose own path
    computation returned `""` silently got `$HOME/workspace`. Unset must keep
    defaulting; set-but-empty must stop the run.
    """


@dataclass
class SyncResult:
    """One repo's verdict. `status` is the answer; everything else is evidence."""

    repo: str
    status: str
    #: The upstream ref this repo was measured against (`origin/main`), or None
    #: when the run never got far enough to have one.
    upstream: str | None = None
    branch: str | None = None
    head_before: str | None = None
    head_after: str | None = None
    #: Commits upstream had that HEAD did not, BEFORE the attempt.
    behind: int = 0
    #: Commits HEAD had that upstream did not. Non-zero ⇒ ff impossible.
    ahead: int = 0
    #: How many commits the local branch actually MOVED. 🔴 Reported separately
    #: from `behind` on purpose: `behind` is the measurement, `moved` is the
    #: outcome, and only a test that asserts the OUTCOME moved can tell a real
    #: fast-forward from a status string.
    moved: int = 0
    #: Dirty paths that INTERSECT what the ff would change — the refusal reason.
    blocking_paths: list[str] = field(default_factory=list)
    blocking_count: int = 0
    #: Dirty paths that do NOT block. Reported, never acted on.
    advisory_paths: list[str] = field(default_factory=list)
    advisory_count: int = 0
    #: git's own stderr, kept verbatim for the statuses where git is the witness.
    detail: str = ""

    @property
    def sentinel(self) -> str:
        return SENTINELS[self.status]

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    @property
    def ok(self) -> bool:
        """0-exit outcomes. NOT "nothing to do" — see `status` for which."""
        return self.exit_code == 0

    def message(self) -> str:
        """One line, sentinel FIRST so a grep for the sentinel finds the repo."""
        head = f"{self.repo}: {self.sentinel}"
        if self.status == STATUS_SYNCED:
            return (
                f"{head} — {self.moved} commit(s) onto {self.upstream} "
                f"({_short(self.head_before)} -> {_short(self.head_after)})"
            )
        if self.status == STATUS_CURRENT:
            return f"{head} with {self.upstream} at {_short(self.head_before)}"
        if self.status == STATUS_REFUSED_DIRTY:
            return (
                f"{head} — {self.blocking_count} path(s) block a "
                f"{self.behind}-commit fast-forward onto {self.upstream}: "
                f"{_render_paths(self.blocking_paths, self.blocking_count)}"
            )
        if self.status == STATUS_REFUSED_DIVERGED:
            return (
                f"{head} — {self.ahead} local commit(s) not on {self.upstream} "
                f"(and {self.behind} behind); a fast-forward is impossible"
            )
        return f"{head}{(' — ' + self.detail) if self.detail else ''}"

    def as_dict(self) -> dict:
        """The machine-readable form. Every field a caller might branch on."""
        return {
            "repo": self.repo,
            "status": self.status,
            "sentinel": self.sentinel,
            "exit_code": self.exit_code,
            "branch": self.branch,
            "upstream": self.upstream,
            "head_before": self.head_before,
            "head_after": self.head_after,
            "behind": self.behind,
            "ahead": self.ahead,
            "moved": self.moved,
            "blocking_paths": self.blocking_paths,
            "blocking_count": self.blocking_count,
            "advisory_paths": self.advisory_paths,
            "advisory_count": self.advisory_count,
            "detail": self.detail,
            "message": self.message(),
        }


@dataclass
class SyncRun:
    """A whole fleet-wide run. `exit_code` is the WORST repo's."""

    results: list[SyncResult] = field(default_factory=list)
    #: `(roots_scanned, clones_found)` when the repo list was DEFAULTED, else
    #: None. The CLI prints it; its existence is what stops a default being a
    #: silent hardcode.
    defaulted_from: tuple[tuple[str, ...], tuple[str, ...]] | None = None

    @property
    def exit_code(self) -> int:
        return worst_exit_code(self.results)


def _short(sha: str | None) -> str:
    return (sha or "?")[:12]


def _render_paths(paths: Sequence[str], total: int) -> str:
    shown = ", ".join(paths)
    if total > len(paths):
        return f"{shown} (+{total - len(paths)} more)"
    return shown


def worst_exit_code(results: Iterable[SyncResult]) -> int:
    """The highest code across repos — a fleet run is only as green as its worst
    clone, and a refusal must never be averaged away by nine successes."""
    return max((r.exit_code for r in results), default=0)


def _git(
    repo: str | Path,
    args: Sequence[str],
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # Never sit waiting for a credential prompt inside an unattended run.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _out(repo: str | Path, args: Sequence[str]) -> str | None:
    """stdout stripped, or None if git refused. Never raises."""
    try:
        proc = _git(repo, args)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def discover_clones(roots: Iterable[str] | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(roots_scanned, primary_clones_found)`, both absolute, both sorted.

    A PRIMARY clone only: in a linked worktree `.git` is a FILE holding
    `gitdir: …`, in the primary clone it is a DIRECTORY. That single test is
    what keeps a fleet-wide default from picking up the 43 foreign worktrees the
    audit found nested under one of these clones — syncing a worktree is not
    this tool's job and would move a branch somebody is working on.

    Roots come from `DEVRC_CLONE_ROOTS` (colon-separated) when set, else
    `DEFAULT_CLONE_ROOTS`. Set-but-empty raises `EmptyRootsError`.
    """
    if roots is None:
        raw = os.environ.get(CLONE_ROOTS_ENV)
        if raw is not None and not raw.strip():
            raise EmptyRootsError(
                f"{CLONE_ROOTS_ENV} is set but empty — refusing to fall back to "
                f"{list(DEFAULT_CLONE_ROOTS)}. Unset it to default, or give it a value."
            )
        roots = raw.split(":") if raw else list(DEFAULT_CLONE_ROOTS)

    scanned: list[str] = []
    found: list[str] = []
    for root in roots:
        if not root:
            continue
        base = Path(root).expanduser()
        scanned.append(str(base))
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if (child / ".git").is_dir():
                found.append(str(child))
    return tuple(scanned), tuple(sorted(set(found)))


def _dirty_entries(repo: str | Path) -> list[str] | None:
    """Every path git considers dirty: staged, unstaged, and untracked.

    Parsed from `--porcelain -z` rather than the line-oriented form, because a
    path with a newline or a quote in it is rendered C-quoted in the default
    output and would then be intersected under the WRONG name — a rename also
    emits two NUL-separated fields, which the line form joins with " -> ".
    Untracked DIRECTORIES are reported as `dir/`, and the intersection below
    treats a trailing slash as a prefix for exactly that reason.
    """
    try:
        proc = _git(repo, ["status", "--porcelain", "-z", "--untracked-files=normal"])
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    fields = proc.stdout.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        # "XY <path>" — the status is two columns and one space.
        xy, path = entry[:2], entry[3:]
        if not path:
            continue
        paths.append(path)
        # A rename/copy carries its ORIGIN as the next NUL-separated field. Both
        # names matter: the ff can collide with either side.
        if "R" in xy or "C" in xy:
            if i < len(fields) and fields[i]:
                paths.append(fields[i])
                i += 1
    return paths


def _changed_by_ff(repo: str | Path, upstream: str) -> list[str] | None:
    """The paths a fast-forward onto `upstream` would write.

    `git diff --name-only HEAD <upstream>` and NOT
    `git diff --quiet HEAD <upstream> -- <path>` per path: that form exits 0 when
    the path exists on NEITHER side, which reads as a reassuring "unchanged" for a
    path that is simply absent (`claude/RULES.md` → a comparison against an absent
    operand reports SAME, not MISSING). `--name-only` lists content, so an absent
    path is simply not listed and cannot be mistaken for an unchanged one.

    `--no-renames` because rename detection reports only the DESTINATION, and the
    ff writes BOTH ends: it deletes the source path too. A rename-detected list
    would omit the source and score local work sitting at that path as
    non-blocking.
    """
    out = _out(repo, ["diff", "--no-renames", "--name-only", "HEAD", upstream])
    if out is None:
        return None
    return [p for p in out.splitlines() if p]


def _blocking(dirty: Sequence[str], changed: Sequence[str]) -> list[str]:
    """Dirty paths the ff would also write.

    An untracked DIRECTORY (`dir/`) blocks any changed path beneath it: git
    reports the directory, not its files, so a plain set intersection would score
    `dir/` against `dir/file` as no overlap and let the merge run into it.
    """
    changed_set = set(changed)
    hits: list[str] = []
    for d in dirty:
        if d in changed_set:
            hits.append(d)
        elif d.endswith("/") and any(c.startswith(d) for c in changed):
            hits.append(d)
    # Stable, de-duplicated, and sorted so the report is reproducible.
    return sorted(set(hits))


def sync_repo(
    repo: str | Path,
    *,
    fetch: bool = True,
    fetch_timeout: float = FETCH_TIMEOUT_SECS,
) -> SyncResult:
    """Classify — and, where it is safe, perform — a fast-forward of ONE clone.

    The order of the guards is the order of the questions, cheapest and most
    fundamental first, and each one exits with its own status rather than falling
    through to a neighbouring one.
    """
    repo_s = str(repo)
    res = SyncResult(repo=repo_s, status=STATUS_CURRENT)

    # 1. Is this a git repository at all? A path that was renamed or never
    #    existed must not be reported as "nothing to do".
    if _out(repo_s, ["rev-parse", "--git-dir"]) is None:
        res.status = STATUS_REFUSED_NOT_A_REPO
        res.detail = f"{repo_s} is not a git repository (or git could not read it)"
        return res

    # 2. Is HEAD on a branch? A detached HEAD has nothing to fast-forward; there
    #    is no branch ref to move, and moving HEAD alone would strand commits.
    branch = _out(repo_s, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    if not branch:
        res.status = STATUS_REFUSED_DETACHED
        res.head_before = _out(repo_s, ["rev-parse", "HEAD"])
        res.detail = "HEAD is not on a branch — there is no branch ref to advance"
        return res
    res.branch = branch
    res.head_before = _out(repo_s, ["rev-parse", "HEAD"])

    # 3. Where would it fast-forward TO? The branch's OWN upstream, never a
    #    guessed mainline — see the module docstring.
    remote = _out(repo_s, ["config", "--get", f"branch.{branch}.remote"])
    upstream = _out(repo_s, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if not upstream:
        res.status = STATUS_REFUSED_NO_UPSTREAM
        res.detail = (
            f"branch '{branch}' has no upstream configured — "
            "refusing to guess a target (a mainline is a different question)"
        )
        return res
    res.upstream = upstream

    # 4. Refresh the remote-tracking refs. Without this the whole run measures a
    #    stale ref and reports `current` on a clone 606 commits behind — the
    #    exact false green this tool exists to remove.
    if fetch:
        if not remote:
            res.status = STATUS_REFUSED_NO_UPSTREAM
            res.detail = (
                f"branch '{branch}' tracks '{upstream}' but has no "
                f"branch.{branch}.remote — nothing to fetch from"
            )
            return res
        try:
            proc = _git(repo_s, ["fetch", "--quiet", remote], timeout=fetch_timeout)
        except subprocess.TimeoutExpired:
            res.status = STATUS_REFUSED_FETCH_FAILED
            res.detail = f"fetch from '{remote}' exceeded {fetch_timeout:g}s"
            return res
        except (OSError, subprocess.SubprocessError) as exc:
            res.status = STATUS_REFUSED_FETCH_FAILED
            res.detail = f"fetch from '{remote}' could not run: {exc}"
            return res
        if proc.returncode != 0:
            why = (proc.stderr or proc.stdout).strip()[:400]
            res.status = STATUS_REFUSED_FETCH_FAILED
            res.detail = f"fetch from '{remote}' exited {proc.returncode}: {why}"
            return res

    # 5. How far apart are they? `--left-right --count` gives both numbers from
    #    one command, and both are needed: `ahead` decides whether an ff is even
    #    possible, `behind` is the delta a `synced` result must report.
    counts = _out(repo_s, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
    if counts is None:
        res.status = STATUS_REFUSED_NO_UPSTREAM
        res.detail = f"could not measure HEAD...{upstream} — does '{upstream}' exist here?"
        return res
    parts = counts.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        res.status = STATUS_REFUSED_NO_UPSTREAM
        res.detail = f"unparseable rev-list output for HEAD...{upstream}: {counts!r}"
        return res
    res.ahead, res.behind = int(parts[0]), int(parts[1])

    # 6. DIVERGED before DIRTY: a clone with local commits cannot be
    #    fast-forwarded whatever its working tree looks like, and reporting the
    #    dirt instead would send the human to clean a tree that would still not
    #    fast-forward afterwards. Ahead-only (behind == 0) lands here too — it is
    #    not `current`, because HEAD and upstream genuinely differ.
    if res.ahead > 0:
        res.status = STATUS_REFUSED_DIVERGED
        res.head_after = res.head_before
        res.detail = (
            f"'{branch}' is {res.ahead} ahead of '{upstream}' "
            f"({res.behind} behind) — push, rebase, or move the work off this clone"
        )
        return res

    # 7. Nothing to do. Deliberately NOT the same answer as `synced`.
    if res.behind == 0:
        res.status = STATUS_CURRENT
        res.head_after = res.head_before
        return res

    # 8. Would the ff walk into local work? Overlap, not a boolean — see docstring.
    dirty = _dirty_entries(repo_s)
    changed = _changed_by_ff(repo_s, upstream)
    if dirty is None or changed is None:
        res.status = STATUS_REFUSED_FF_FAILED
        res.head_after = res.head_before
        res.detail = (
            "could not read the working-tree state or the incoming path list; "
            "refusing to fast-forward blind"
        )
        return res

    blocking = _blocking(dirty, changed)
    if blocking:
        res.status = STATUS_REFUSED_DIRTY
        res.head_after = res.head_before
        res.blocking_count = len(blocking)
        res.blocking_paths = blocking[:DIRTY_PATH_CAP]
        advisory = sorted(set(dirty) - set(blocking))
        res.advisory_count = len(advisory)
        res.advisory_paths = advisory[:DIRTY_PATH_CAP]
        res.detail = (
            "uncommitted or untracked changes overlap the incoming commits; "
            "resolve them by hand — this tool never stashes, resets or discards"
        )
        return res

    advisory = sorted(set(dirty))
    res.advisory_count = len(advisory)
    res.advisory_paths = advisory[:DIRTY_PATH_CAP]

    # 9. The merge. git is the final authority; a refusal here is its OWN status.
    try:
        proc = _git(repo_s, ["merge", "--ff-only", upstream])
    except (OSError, subprocess.SubprocessError) as exc:
        res.status = STATUS_REFUSED_FF_FAILED
        res.head_after = res.head_before
        res.detail = f"git merge --ff-only could not run: {exc}"
        return res

    res.head_after = _out(repo_s, ["rev-parse", "HEAD"])
    if proc.returncode != 0:
        res.status = STATUS_REFUSED_FF_FAILED
        res.detail = (
            "the pre-check found no blocking path but git still refused: "
            + (proc.stderr or proc.stdout).strip()[:400]
        )
        return res

    # 🔴 The OUTCOME, measured — not inferred from the exit code. A merge that
    #    reports success while HEAD sits where it started is not a sync, and
    #    calling it one is the false green this whole module is built against.
    if res.head_after == res.head_before:
        res.status = STATUS_REFUSED_FF_FAILED
        res.detail = (
            f"git merge --ff-only reported success but HEAD did not move from "
            f"{_short(res.head_before)} while {res.behind} commit(s) behind"
        )
        return res

    moved = _out(repo_s, ["rev-list", "--count", f"{res.head_before}..HEAD"])
    res.moved = int(moved) if moved and moved.isdigit() else res.behind
    res.status = STATUS_SYNCED
    return res


def sync_many(
    repos: Sequence[str] | None = None,
    *,
    fetch: bool = True,
    fetch_timeout: float = FETCH_TIMEOUT_SECS,
    roots: Iterable[str] | None = None,
) -> SyncRun:
    """Classify every named clone — or, with no names, every DISCOVERED one.

    The fleet is the point: several shared clones drift independently, and a
    helper that can only be pointed at one of them leaves the others exactly as
    stale as the audit found them.
    """
    run = SyncRun()
    if repos:
        targets = list(repos)
    else:
        scanned, found = discover_clones(roots)
        run.defaulted_from = (scanned, found)
        targets = list(found)
    for repo in targets:
        run.results.append(sync_repo(repo, fetch=fetch, fetch_timeout=fetch_timeout))
    return run
