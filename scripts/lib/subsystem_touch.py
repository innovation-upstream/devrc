#!/usr/bin/env python3
"""Report which `/analyze-service` index entries a session's changed paths touch.

The WRITER-side companion to `scripts/lib/subsystem_resolver.py`, and the one
piece the subsystem-knowledge store was missing: a way for an entry to come into
existence from ORDINARY WORK rather than only from infra recon.

`claudedocs/decision-subsystem-store-rejected-2026-08-11.md` measured the gap and
the constraint on closing it:

  * the store's only writer is `/analyze-service`, so 21 entries exist in ONE
    scope while work spans ~12 repos. "No non-infra entries" was never evidence
    of no demand — nothing could create one.
  * opt-in survives when it RIDES a ritual already performed, and dies when it
    IS the ritual. Six slash-commands have never been invoked once.

So this is one writer, one verb, riding `/handoff`. It reports; the SKILL does
the writing, confirm-gated and diff-first.

🔴 THIS MODULE NEVER WRITES TO THE STORE. Not "does not today" — there is no
write path here at all, and `TestNeverWrites` hashes a store tree either side of
every mode to keep it that way. The store is curated, client-confidential and
has no off-machine backup; the confirm gate lives in the skill, where a human
sees the diff, and a helper that could write would be a second, ungated writer.

🔴 IT DOES NOT REIMPLEMENT MATCHING. Normalization, kind-splitting, tier
resolution and path→entry association all come from `subsystem_resolver`, which
is the executable authority (`claude/RULES.md` → "One rule, one place": a
predicate open-coded at N sites is typically wrong at N−1 of them). What is new
here is only: where the paths come from, how a MISS is turned into a
nomination, and the census that makes the experiment falsifiable.


WHERE THE PATHS COME FROM, AND WHY
----------------------------------
Three candidate sources were available; the choice is GIT, bounded to the
branch, with the bound stated in the output. Reasoning, so it can be overturned
with evidence rather than taste:

  * TELEMETRY (`kind=session-summary`'s `changed_paths`) is the source P1 of the
    decision doc will use, and it is the most honest one — it is per-SESSION. It
    is also unavailable here: `/handoff` fires at the END of a session, before
    that session has settled or been summarised, so at the moment this runs the
    row does not exist yet. Waiting for it would move the write off the ritual,
    which is the exact property that made every other opt-in tool die.
  * THE AGENT'S OWN MEMORY of what it edited is available and is what a human
    would use — and is not deterministic. It cannot be re-run to the same
    answer, cannot be tested, and is precisely the "prose/heuristic" fix
    `claude/RULES.md` says to prefer a structural one over.
  * GIT is available immediately, is deterministic, is re-runnable to the same
    answer, and — the deciding property — `/handoff` step 1 ALREADY runs
    `git status -sb` / `git log` / `git diff --stat`. So the path set is a free
    side-effect of recon the ritual performs anyway, which is the same shape
    that made `/analyze-service`'s index write stick.

Git's known weakness is authorship: `git log` does not know which session
authored a commit. That is not fixed here, it is BOUNDED and DECLARED. The
window is:

    (working tree vs HEAD, incl. untracked)  ∪  (merge-base(HEAD, base)..HEAD)

i.e. uncommitted work plus THIS BRANCH's own commits — under the repo's standing
feature-branch rule, the closest deterministic proxy for "this session". Every
report says `window: branch` and every renderer prints the caveat, because
"what this branch touched" is a strictly weaker claim than "what this session
touched" and rounding the two together is how a proxy becomes a lie. On a branch
that IS the base (or a detached HEAD with no base) the commit half is empty and
the window degrades to `worktree` — SAID, not silently emptied, because an
empty commit window and a branch with no commits are different facts.

A caller with a better path set may supply one (`--paths-from -`); the reporting
core takes paths as an argument and has never heard of git. That is what lets P1
feed the same core from telemetry later without touching this file's logic.


SCOPE COMES FROM THE REPO, AND A WORKTREE IS NOT ITS OWN REPO
-------------------------------------------------------------
`analyze-service/SKILL.md`: "`<scope>` defaults to the basename of the owning
repo root". Taken literally via `git rev-parse --show-toplevel`, every agent
worktree would become its OWN scope — `agent-a9f80ada5bf8837e4` — and the store
would shard into hundreds of one-entry scopes that no later run could ever
resolve back. `derive_scope` reads the git COMMON dir instead, so a worktree and
its base clone agree. See that function; `TestScopeDerivation` is the guard.


CONTRACT SUMMARY
----------------
    derive_scope(repo_root, git_common_dir)   -> str
    collect_git_paths(repo)                   -> PathSource      (runs git)
    caller_supplied(paths)                    -> PathSource      (pure)
    nominate(assoc, index, *, min_paths, limit)
                                              -> tuple[Nomination, ...]
    build_report(source, store_root, scope, *, today, ...)
                                              -> TouchReport
    render_text(report) / report_json(report) -> str / dict
    census(store_root)                        -> Census
    main(argv)                                -> int

Every failure mode carries a distinct sentinel phrase, so a caller — or a
mutation test — can tell WHICH guard fired rather than merely that one did:

    "store root not found"       StoreMissingError
    "scope not in store"         -> status `scope-absent` (NOT an error: a repo
                                    with no entries yet is the ordinary case and
                                    the entire point of this change)
    "invalid repo-relative path" InvalidPathError, re-raised from the resolver
    "git command failed"         GitError

🔴 THE FAILURE MODE IS A CONFIDENT ZERO. "Nothing to record" is the observable
that the most causes share: no paths, an unknown scope, a matcher wired to
nothing, entries reached but below threshold, and a genuinely untouched index
all render as an empty proposal list. `TouchReport.status` names WHICH, in four
values that share no spelling, and the below-threshold case is called out
separately by the renderer — `claude/RULES.md` → "An EMPTY RESULT cannot
distinguish two mechanisms". A missing store root is deliberately NOT one of
those values: it raises, because it is a broken environment rather than a
reading, and a status nothing could emit would be a declaration with no code
path behind it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from subsystem_resolver import (  # noqa: E402
    DEFAULT_MIN_PATHS,
    AmbiguousRefError,
    Association,
    ResolverError,
    SubsystemIndex,
    UnknownScopeError,
    associate_paths,
    load_index,
    normalize_ref,
    parse_front_matter,
    path_refs,
    resolve_ref_tiered,
)

__all__ = [
    "WRITER_ID",
    "DEFAULT_STORE_ROOT",
    "DEFAULT_NOMINATION_LIMIT",
    "BASE_REF_CANDIDATES",
    "TouchError",
    "StoreMissingError",
    "GitError",
    "PathSource",
    "Nomination",
    "TouchReport",
    "Census",
    "derive_scope",
    "collect_git_paths",
    "caller_supplied",
    "nominate",
    "build_report",
    "render_text",
    "report_json",
    "new_entry_template",
    "journal_line_shape",
    "census",
    "main",
]

# 🔴 THE FALSIFIABILITY STAMP. Every entry this writer proposes carries
# `created_by: handoff` in its front matter, so the question the decision doc
# left open — "do entries accrue OUTSIDE infra recon, or was the single-scope
# corpus evidence of no demand?" — is answered by counting, not by recollection.
# `census()` is the counter. The value is the SKILL name, not this file's name,
# because the skill is the thing a later reader will be trying to attribute.
WRITER_ID = "handoff"

# Writers that stamp themselves. An entry with NO `created_by:` is neither of
# them: it predates the stamp. `census()` reports that bucket separately and
# never folds it into either writer — attributing 21 pre-existing entries to
# `analyze-service` would be an inference, and the whole point of the field is
# to stop inferring.
KNOWN_WRITERS: tuple[str, ...] = ("analyze-service", "handoff")
UNSTAMPED = "unstamped (pre-instrumentation)"

DEFAULT_STORE_ROOT = Path.home() / ".claude" / "analyze-service-index"

# `analyze-service/SKILL.md` on auto-discovered pointers: "propose at most ~5-7
# candidates, never a raw match list — a dump is unusable even though the human
# confirms each". Same reasoning, same order of magnitude, applied to entry
# nominations: a confirm gate a human stops reading is not a confirm gate.
DEFAULT_NOMINATION_LIMIT = 5

# Tried in order; the FIRST that exists wins. Remote-tracking refs come first so
# that on a branch named `main` with unpushed local commits the window is those
# commits (the diverged-host case CLAUDE.md describes), not empty.
BASE_REF_CANDIDATES: tuple[str, ...] = ("origin/main", "origin/master", "main", "master")

_SENSITIVITY_FAIL_SAFE = "client-confidential"


# --- Errors --------------------------------------------------------------------


class TouchError(Exception):
    """Base for every error this module raises."""


class StoreMissingError(TouchError):
    """The store root does not exist. Sentinel: 'store root not found'."""


class GitError(TouchError):
    """A git invocation failed. Sentinel: 'git command failed'."""


# --- Scope ---------------------------------------------------------------------


def derive_scope(repo_root: str | Path, git_common_dir: str | Path) -> str:
    """The store scope for a repo, normalized. Worktree-stable.

    `git_common_dir` is `git rev-parse --path-format=absolute --git-common-dir`:
    for BOTH a base clone and any worktree of it, that is the base clone's
    `.git`, so its parent is the repo everyone means. `--show-toplevel` is not
    used for this because in a worktree it is the worktree's own directory.

    Fallback: when the common dir is not literally named `.git` — a bare repo,
    or a submodule whose common dir is `<super>/.git/modules/<name>` — the
    parent basename would be meaningless (`modules`), so the repo root's
    basename is used instead. Stated because the fallback is silent otherwise.
    """
    common = Path(git_common_dir)
    if common.name == ".git":
        return normalize_ref(common.parent.name)
    return normalize_ref(Path(repo_root).name)


# --- Path sources --------------------------------------------------------------


@dataclass(frozen=True)
class PathSource:
    """Paths plus the provenance needed to read a zero honestly.

    `commands` is the argv actually run, not a description of it: when a report
    says "0 paths", the next question is always "what did you ask?", and an
    answer that was written by hand can be wrong about the code beside it.
    """

    kind: str
    """`git` or `caller`."""

    window: str
    """`branch` (worktree ∪ this branch's commits), `worktree`, or `supplied`."""

    paths: tuple[str, ...]
    base_ref: str | None = None
    commands: tuple[tuple[str, ...], ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def caveat(self) -> str:
        """The claim this source can actually support. Never widened by a caller."""
        if self.kind == "caller":
            return "paths were supplied by the caller; provenance is the caller's to state"
        if self.window == "branch":
            return (
                f"git: uncommitted work + this branch's own commits since "
                f"{self.base_ref} — what this BRANCH touched, NOT what this SESSION "
                f"touched (git does not record the session that authored a commit)"
            )
        return (
            "git: uncommitted work only — HEAD has no branch window (on the base "
            "ref, or no base ref found), so committed work is NOT represented"
        )


def caller_supplied(paths: Iterable[str]) -> PathSource:
    """Wrap an externally-supplied path set. Pure; no git, no clock."""
    seen: list[str] = []
    for p in paths:
        p = p.strip()
        if p and p not in seen:
            seen.append(p)
    return PathSource(kind="caller", window="supplied", paths=tuple(seen))


def _git(repo: Path, args: Sequence[str]) -> str:
    argv = ["git", "-C", str(repo), *args]
    env = dict(os.environ)
    # Read-only invocations must not take the index lock: a concurrent agent in
    # the same checkout is the normal case in this repo, and a helper that can
    # block someone else's commit is not read-only in the way that matters.
    env["GIT_OPTIONAL_LOCKS"] = "0"
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise GitError(
            f"git command failed ({' '.join(argv)}): exit {proc.returncode}: "
            f"{proc.stderr.strip() or '(no stderr)'}"
        )
    return proc.stdout


def _git_ok(repo: Path, args: Sequence[str]) -> bool:
    try:
        _git(repo, args)
    except GitError:
        return False
    return True


def _nul_list(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def collect_git_paths(
    repo: str | Path,
    *,
    base_ref_candidates: Sequence[str] = BASE_REF_CANDIDATES,
    exclude: Iterable[str] = (),
) -> PathSource:
    """The branch-bounded path window described in the module docstring.

    Three read-only invocations, each `-z` so a path containing a space or a
    newline cannot be split into two plausible components (which would
    manufacture refs out of nothing):

      1. `diff --name-only -z HEAD`            tracked, staged + unstaged
      2. `ls-files --others --exclude-standard -z`   untracked, gitignore honoured
      3. `diff --name-only -z <merge-base>..HEAD`    this branch's own commits

    (3) is skipped, and `window` degrades to `worktree`, when no base ref exists
    or the merge-base IS HEAD. Both are reported in `notes` rather than showing
    up as a smaller number with no explanation.

    🔴 EVERY INVOCATION RUNS AGAINST THE TOPLEVEL, NOT THE CALLER'S DIRECTORY.
    The three commands do not share a path frame: `diff --name-only` is always
    repo-root-relative, while `ls-files --others` is **cwd-relative AND
    cwd-scoped**. Called with a subdirectory, the two would return paths in
    different frames — untracked paths stripped of their prefix, and untracked
    files elsewhere in the repo missing entirely — so components would be both
    manufactured (`tests/x.py` read as if at the root) and lost. Resolving to
    the toplevel FIRST makes one frame for all three; it is not a convenience.

    `exclude` drops repo-relative paths from the window after collection, for a
    caller that knows a path is an artifact of the ritual doing the collecting
    (`/handoff` writes its own doc, then asks what changed). Exclusions are
    COUNTED into `notes`, never silently dropped.
    """
    # 🔴 One frame for every command below. See the docstring.
    toplevel = _git(Path(repo), ["rev-parse", "--show-toplevel"]).strip()
    repo = Path(toplevel)
    excluded = {e.strip() for e in exclude if e.strip()}
    commands: list[tuple[str, ...]] = []
    notes: list[str] = []
    paths: list[str] = []
    dropped: list[str] = []

    def add(new: Iterable[str]) -> None:
        for p in new:
            if p in excluded:
                if p not in dropped:
                    dropped.append(p)
                continue
            if p not in paths:
                paths.append(p)

    worktree_args = ["diff", "--name-only", "-z", "HEAD"]
    commands.append(tuple(worktree_args))
    add(_nul_list(_git(repo, worktree_args)))

    untracked_args = ["ls-files", "--others", "--exclude-standard", "-z"]
    commands.append(tuple(untracked_args))
    add(_nul_list(_git(repo, untracked_args)))

    base_ref: str | None = None
    for cand in base_ref_candidates:
        if _git_ok(repo, ["rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"]):
            base_ref = cand
            break

    window = "worktree"
    if base_ref is None:
        notes.append(
            f"no base ref among {', '.join(base_ref_candidates)}; committed work is not "
            f"in the window"
        )
    else:
        try:
            merge_base = _git(repo, ["merge-base", "HEAD", base_ref]).strip()
        except GitError:
            merge_base = ""
            notes.append(f"HEAD and {base_ref} share no history; committed work is not in the window")
        head = _git(repo, ["rev-parse", "HEAD"]).strip() if merge_base else ""
        if merge_base and merge_base == head:
            notes.append(
                f"HEAD is at the merge-base with {base_ref} — no branch commits to add "
                f"(you are on the base ref, or have not committed yet)"
            )
        elif merge_base:
            branch_args = ["diff", "--name-only", "-z", f"{merge_base}..HEAD"]
            commands.append(tuple(branch_args))
            add(_nul_list(_git(repo, branch_args)))
            window = "branch"

    if dropped:
        notes.append(
            f"excluded {len(dropped)} caller-named path(s) from the window: "
            f"{', '.join(dropped)}"
        )

    return PathSource(
        kind="git",
        window=window,
        paths=tuple(paths),
        base_ref=base_ref,
        commands=tuple(commands),
        notes=tuple(notes),
    )


# --- Nomination ----------------------------------------------------------------


@dataclass(frozen=True)
class Nomination:
    """A ref that NOTHING in the index resolves, offered as a candidate entry.

    A nomination is not a decision and never becomes one here: the skill shows
    it, a human confirms it, and the entry it would create is thin on purpose
    ("a thin entry that exists beats a rich one that doesn't").
    """

    ref: str
    paths: tuple[str, ...]
    depth: int
    """Deepest component index this ref was seen at — the specificity tiebreak."""

    coherent: bool
    """True when every covered path shares the prefix ending at this ref.

    🔴 The property that separates "a directory the work lives in" from "a
    filename that recurs across unrelated directories", WITHOUT a stoplist of
    generic names. A ref like `values` can cover twenty paths and outrank every
    real candidate on count alone, while those twenty paths sit in twenty
    DIFFERENT subsystems — the one thing `values` is certainly not. A subsystem
    is a place in the tree, so a candidate for one is a ref whose covered paths
    agree on where that place is.

    Non-coherent refs are RANKED BELOW, and the top one is never DROPPED: a code
    subsystem genuinely spread across `lib/<name>.py` and `tests/<name>.py` is
    non-coherent and is still the only sensible candidate for those two paths.
    🔴 That sentence used to be false. Coherence is a PRIMARY sort key, so with
    enough coherent refs above it the best non-coherent one fell past
    `limit` and was cut — measured landing 11th against a limit of 5. `nominate`
    now reserves the last slot for it; see `_reserve_slot_for_top_noncoherent`.
    """

    fans_out: bool
    """True when this ref's covered paths branch into ≥2 distinct SUBDIRECTORIES.

    🔴 The umbrella discriminator, and the second half of replacing a stoplist.
    Coherence alone fixed the recurring-filename case and left the opposite one
    open: a top-level directory covers everything beneath it, so it wins on
    count every time — on this module's own PR diff the top five were all
    `scripts`/`skills`/`claude`-shaped, and none of them is a thing anyone would
    journal against.

    Counted over NON-TERMINAL children only: for `apps/ingest/a.yaml`, `apps`
    has the child `ingest` (a directory) while `ingest`'s children are files.
    That is what keeps the leaf directory — the actual subsystem — unpenalised
    while its parent is penalised for spanning several.
    """

    @property
    def path_count(self) -> int:
        return len(self.paths)


def nominate(
    assoc: Association,
    index: SubsystemIndex,
    *,
    min_paths: int = DEFAULT_MIN_PATHS,
    limit: int = DEFAULT_NOMINATION_LIMIT,
) -> tuple[Nomination, ...]:
    """Candidate entry names, from the paths that resolved to NOTHING.

    Deterministic, and deliberately built out of the same primitives the matcher
    uses — `path_refs` for the candidate refs and the SAME `min_paths` for the
    threshold — so a name that would be nominated today is a name that would
    match tomorrow once the entry exists. A separate heuristic here (a stoplist
    of "generic" directory names, say) would be a second predicate that drifts
    from the first, and `claude/RULES.md` says to prefer the structural fix and
    to declare the heuristic if you reach for one. There is no stoplist.

    Ranking, in order — the two structural keys stand in for the stoplist of
    "generic" names that a heuristic would have needed:

      1. COHERENT first          (`Nomination.coherent` — kills the recurring
                                  filename that covers unrelated directories)
      2. NON-FANNING-OUT first   (`Nomination.fans_out` — kills the top-level
                                  umbrella that covers everything beneath it)
      3. distinct-path count desc
      4. DEPTH desc              (specificity: when two components cover exactly
                                  the same paths, the deeper one is the more
                                  specific name for that set)
      5. ref asc                 (determinism)

    Then `_reserve_slot_for_top_noncoherent` runs, so key 1 can never silently
    DELETE the only sensible candidate for a cross-directory subsystem.

    🔴 A ref that ALREADY resolves — or that is AMBIGUOUS — is never nominated.
    `unmatched_paths` almost guarantees the first (a path lands there only if no
    component resolved), but an ambiguous ref is the live case: the resolver
    refuses to pick, the path stays unmatched, and nominating it would propose
    CREATING a third entry for a name that already names two. The re-check
    below is that guard, and it is reachable — `TestNominationGuards` reaches it
    with the resolver's own ambiguity fixture.
    """
    by_ref: dict[str, list[str]] = {}
    depth_by_ref: dict[str, int] = {}
    prefixes_by_ref: dict[str, set[tuple[str, ...]]] = {}
    children_by_ref: dict[str, set[str]] = {}
    for path in assoc.unmatched_paths:
        parts = [p for p in path.split("/") if p not in ("", ".")]
        for component, ref in path_refs(path):
            bucket = by_ref.setdefault(ref, [])
            if path not in bucket:
                bucket.append(path)
            try:
                depth = parts.index(component)
            except ValueError:
                # A filename STEM is not itself a component; it belongs to the
                # last one. Ranked at that depth rather than dropped — a file
                # named for the subsystem is a perfectly good nomination. It is
                # then non-coherent by construction (its prefix is the whole
                # path, which differs per path), which is exactly right: it
                # ranks below any directory the same paths agree on.
                depth = len(parts) - 1
            depth_by_ref[ref] = max(depth_by_ref.get(ref, 0), depth)
            prefixes_by_ref.setdefault(ref, set()).add(tuple(parts[: depth + 1]))
            # A child is NON-TERMINAL — a directory — when it is not the last
            # part of the path. Counting terminal children (files) would
            # penalise every leaf directory for holding more than one file,
            # which is the opposite of the intent.
            kids = children_by_ref.setdefault(ref, set())
            if depth + 1 < len(parts) - 1:
                kids.add(parts[depth + 1])

    out: list[Nomination] = []
    for ref, paths in by_ref.items():
        if len(paths) < min_paths:
            continue
        try:
            entry, _tier = resolve_ref_tiered(ref, index, assoc.scope)
        except AmbiguousRefError:
            continue
        # ⚠ REDUNDANT-BUT-KEPT, labelled so a mutation sweep does not re-derive
        # it as a live guard: `paths` here comes from `unmatched_paths`, and a
        # path lands there only when NO component of it resolved — so this can
        # never be true today and a mutation of it is UNKILLABLE. It stays
        # because it is the coupling made visible: a future caller that passes
        # `considered_paths` instead would otherwise silently propose creating
        # entries that already exist.
        if entry is not None:
            continue
        out.append(
            Nomination(
                ref=ref,
                paths=tuple(paths),
                depth=depth_by_ref[ref],
                coherent=len(prefixes_by_ref[ref]) == 1,
                fans_out=len(children_by_ref.get(ref, ())) >= 2,
            )
        )

    out.sort(key=lambda n: (not n.coherent, n.fans_out, -n.path_count, -n.depth, n.ref))
    return _reserve_slot_for_top_noncoherent(out, limit)


def _reserve_slot_for_top_noncoherent(
    ranked: Sequence[Nomination], limit: int
) -> tuple[Nomination, ...]:
    """Truncate to `limit`, but never let the coherence key DELETE a candidate.

    🔴 `Nomination.coherent` promises non-coherent refs are "ranked below, not
    dropped". As a primary sort key it did drop them: with enough coherent refs
    ahead, the best non-coherent one — a code subsystem spread across
    `lib/<name>.py` and `tests/<name>.py`, often the ONLY sensible candidate —
    fell past `limit` and vanished. Measured at rank 11 against a limit of 5.

    So the last slot is reserved for the top non-coherent ref whenever the
    straight truncation would have excluded every one of them. The alternative
    considered and rejected was demoting coherence to a secondary key, which
    re-opens the recurring-filename case it was added to close: a `values` ref
    covering six paths would again outrank the three real directories under it.

    A no-op when the cut already contains a non-coherent ref, when there are
    none, or when nothing was truncated — so the ordinary case is untouched.
    """
    head = list(ranked[:limit])
    if limit < 1 or len(ranked) <= limit:
        return tuple(head)
    if any(not n.coherent for n in head):
        return tuple(head)
    promoted = next((n for n in ranked[limit:] if not n.coherent), None)
    if promoted is None:
        return tuple(head)
    return tuple(head[: limit - 1] + [promoted])


# --- The report ----------------------------------------------------------------


@dataclass(frozen=True)
class TouchReport:
    """One deterministic answer to "what did this session touch?".

    🔴 `status` exists because every interesting outcome here renders as an
    empty proposal list, and five different mechanisms produce that emptiness.
    Reading `len(known) == 0` tells a consumer nothing about which.
    """

    status: str
    """`looked-at-nothing` | `scope-absent` | `resolved` | `no-match`.

    ⚠ There is deliberately NO `no-store` value. An absent store root RAISES
    `StoreMissingError` — it is a broken environment, not a reading — and a
    status constant no code path could ever emit would make `STATUS_PRECEDENCE`
    read as four reachable outcomes plus one phantom, which is exactly the
    "a declaration is not a code path" shape this module argues against
    elsewhere. `TestStatusIsTheDiscriminator` reaches all four.
    """

    scope: str
    store_root: str
    source: PathSource
    today: str
    min_paths: int
    association: Association | None = None
    nominations: tuple[Nomination, ...] = ()
    entry_files: Mapping[str, str] = field(default_factory=dict)
    """ref -> the filename in the scope dir, for the append target."""

    @property
    def known(self) -> tuple:
        return self.association.matched if self.association else ()

    @property
    def ambiguous(self) -> tuple:
        return self.association.ambiguous if self.association else ()

    @property
    def below_threshold(self) -> tuple:
        return self.association.below_threshold if self.association else ()

    @property
    def writes_proposed(self) -> bool:
        return bool(self.known) or bool(self.nominations)


# The status precedence, stated once so the renderer, the JSON and the tests
# read the SAME rule rather than three expressions of it.
#
#   1. looked-at-nothing — no paths at all. Nothing the store's state could
#      change about this answer, so it outranks every store condition (an absent
#      store then never even gets consulted, and so can never be reported as a
#      matching failure).
#   2. scope-absent      — the store exists, this repo has no scope dir yet.
#                          NOT an error and not a miss: it is the first-entry
#                          case this whole change exists to make reachable.
#   3. resolved          — at least one entry cleared the threshold.
#   4. no-match          — paths were examined; nothing in the index matched.
#
# An absent store root is NOT in this tuple: it raises `StoreMissingError`.
# Every member here is emitted by `build_report` and reached by a test.
STATUS_PRECEDENCE: tuple[str, ...] = (
    "looked-at-nothing",
    "scope-absent",
    "resolved",
    "no-match",
)


def build_report(
    source: PathSource,
    store_root: str | Path,
    scope: str,
    *,
    today: str,
    min_paths: int = DEFAULT_MIN_PATHS,
    limit: int = DEFAULT_NOMINATION_LIMIT,
) -> TouchReport:
    """Resolve `source.paths` against the store. Read-only; no clock (`today` in).

    `UnknownScopeError` from the resolver is CAUGHT and turned into
    `scope-absent` rather than propagated. That is deliberate and is the single
    most load-bearing decision in this file: under the old writer a repo with no
    entries was an error condition, and under this one it is the ordinary first
    run in every repo that is not the infra repo. An exception there would make
    the intended case the failing case.
    """
    store = Path(store_root)
    if not source.paths:
        return TouchReport(
            status="looked-at-nothing",
            scope=scope,
            store_root=str(store),
            source=source,
            today=today,
            min_paths=min_paths,
        )
    if not store.is_dir():
        raise StoreMissingError(
            f"store root not found: {store} — expected the `/analyze-service` index "
            f"store; nothing was resolved and nothing should be written"
        )

    index = load_index(store)
    try:
        assoc = associate_paths(source.paths, index, scope, min_paths=min_paths)
    except UnknownScopeError:
        # Resolve against an EMPTY scope so the nomination machinery still runs:
        # a repo with no scope dir is exactly where the first entry should come
        # from, and returning nothing here would make the new-scope case the one
        # case that produces no proposal.
        empty = SubsystemIndex(by_scope={normalize_ref(scope): ()})
        assoc = associate_paths(source.paths, empty, scope, min_paths=min_paths)
        return TouchReport(
            status="scope-absent",
            scope=scope,
            store_root=str(store),
            source=source,
            today=today,
            min_paths=min_paths,
            association=assoc,
            nominations=nominate(assoc, empty, min_paths=min_paths, limit=limit),
        )

    noms = nominate(assoc, index, min_paths=min_paths, limit=limit)
    entry_files = {m.entry.ref: m.entry.filename for m in assoc.matched}
    return TouchReport(
        status="resolved" if assoc.matched else "no-match",
        scope=scope,
        store_root=str(store),
        source=source,
        today=today,
        min_paths=min_paths,
        association=assoc,
        nominations=noms,
        entry_files=entry_files,
    )


# --- Proposal shapes -----------------------------------------------------------


def journal_line_shape(today: str) -> str:
    """The existing bullet style, from the live corpus — not a new format.

    Every entry's `## Nuance / work-history` is dated bullets, newest-first,
    ≤2 lines each. This returns the SHAPE; the text is the agent's to write,
    because a generated line would be exactly the "routine state" the schema's
    bloat discipline forbids appending.
    """
    return f"- {today}: <one line, ≤2 — a gotcha, a decision, or why this was touched>"


def new_entry_template(slug: str, scope: str, *, today: str) -> str:
    """A MINIMAL entry: identity front matter + the two sections that always fill.

    Deliberately not the full schema. The strain test in the decision doc found
    that a rich schema's extra sections came out EMPTY for want of evidence, and
    the adoption evidence says a form nobody completes produces no entry at all.
    `## Nuance / work-history` is omitted from the template and added by the
    journal append — an empty section header is a slot begging to be filled with
    the routine status the bloat rules forbid.

    `sensitivity` is written EXPLICITLY at the fail-safe value even though the
    schema already reads an absent field that way: `public` is "a deliberate
    operator claim a recon run may never infer", and a field that is present and
    wrong is easier to notice than one that is absent and assumed.

    🔴 `aliases:` ships COMMENTED, with the `test_<slug>` case named. Matching is
    exact normalized-component equality, so a test file named `test_<slug>.py`
    has the stem `test-<slug>` and does NOT reach `<slug>` — meaning "the module
    plus its test", the most common two-file change there is, counts ONE path
    and falls under `min_paths` forever. Prefix-stripping would have been the
    other fix and was rejected: `path_refs` is the shared predicate inside the
    doc's hashed region and `/analyze-service` consumes it too, so widening it
    here would change matching for a consumer that never asked. An alias is the
    per-entry answer, and it is useless if the writer never mentions it — which
    is why this line exists rather than a sentence in the skill alone.
    `parse_front_matter` skips `#` lines, so the commented line costs nothing
    until someone uncomments it.
    """
    return (
        "---\n"
        f"service: {slug}\n"
        f"scope: {scope}\n"
        f"sensitivity: {_SENSITIVITY_FAIL_SAFE}\n"
        f"created_by: {WRITER_ID}\n"
        f"# aliases: [{slug.replace('-', '_')}, test_{slug.replace('-', '_')}]"
        "  # uncomment + trim: other spellings, and the test-file stem\n"
        "---\n"
        "\n"
        "## What it is\n"
        "<one line: what this durable thing IS. Not what was done to it today.>\n"
        "\n"
        "## Pointers\n"
        "- <path/slug/skill> — <one clause on why>\n"
        "\n"
        "## Nuance / work-history\n"
        f"{journal_line_shape(today)}\n"
    )


# --- Rendering -----------------------------------------------------------------


def render_text(report: TouchReport) -> str:
    """The agent-facing brief. Deterministic: same report in, same bytes out."""
    src = report.source
    out: list[str] = []
    out.append(f"subsystem-touch: status={report.status} scope={report.scope}")
    out.append(f"  store: {report.store_root}")
    out.append(
        f"  paths: {len(src.paths)} ({src.kind}, window={src.window}, "
        f"min_paths={report.min_paths})"
    )
    out.append(f"  caveat: {src.caveat}")
    for note in src.notes:
        out.append(f"  note: {note}")
    for cmd in src.commands:
        out.append(f"  ran: git {' '.join(cmd)}")

    if report.status == "looked-at-nothing":
        out.append("")
        out.append(
            "NOTHING WAS LOOKED AT — the path window is empty. This is NOT "
            "'nothing touched an entry'; no path was examined at all."
        )
        out.append("Propose no write. Say which window was empty and why.")
        return "\n".join(out)

    if report.status == "scope-absent":
        out.append("")
        out.append(
            f"SCOPE ABSENT — the store has no `{report.scope}/` directory yet. Every "
            f"path below is unresolved because there is nothing to resolve against; "
            f"this is the FIRST-ENTRY case, not a miss."
        )

    if report.known:
        out.append("")
        out.append("KNOWN ENTRIES (propose a dated journal line, confirm-gated):")
        for m in report.known:
            fname = report.entry_files.get(m.entry.ref, m.entry.filename)
            out.append(f"  - {m.entry.ref}  ->  {report.scope}/{fname}  ({m.path_count} paths)")
            for ev in m.evidence[:3]:
                via = f"alias `{ev.matched_alias}`" if ev.matched_alias else f"{ev.tier} `{ev.ref}`"
                out.append(f"      via {via}  <-  {ev.path}")
            if len(m.evidence) > 3:
                out.append(f"      … {len(m.evidence) - 3} more")
        out.append(f"  append shape: {journal_line_shape(report.today)}")
        out.append("  insert as the FIRST bullet under `## Nuance / work-history`.")

    if report.below_threshold:
        out.append("")
        out.append(
            f"BELOW THRESHOLD (<{report.min_paths} paths — reported, not proposed):"
        )
        for m in report.below_threshold:
            out.append(f"  - {m.entry.ref}  ({m.path_count} path)")

    if report.ambiguous:
        out.append("")
        out.append("AMBIGUOUS — write NOTHING for these; the ref names more than one entry:")
        for a in report.ambiguous:
            out.append(f"  - `{a.ref}` ({a.tier} tier): {', '.join(a.candidates)}")
            out.append(f"      from: {', '.join(a.paths)}")
        out.append("  Report the candidates and let the operator pick. Never guess.")

    if report.nominations:
        out.append("")
        out.append("NO ENTRY (propose a MINIMAL new entry, confirm-gated — pick at most one):")
        for n in report.nominations:
            shape = "one place in the tree" if n.coherent else "spread across directories"
            if n.fans_out:
                shape += ", umbrella over several"
            out.append(f"  - {n.ref}  ({n.path_count} paths, depth {n.depth}, {shape})")
            for p in n.paths[:3]:
                out.append(f"      {p}")
            if n.path_count > 3:
                out.append(f"      … {n.path_count - 3} more")
        out.append(
            f"  template: {report.scope}/<slug>.md  (identity front matter + "
            f"`## What it is` + `## Pointers`; sensitivity={_SENSITIVITY_FAIL_SAFE}, "
            f"created_by={WRITER_ID})"
        )

    if not report.writes_proposed:
        out.append("")
        n = len(src.paths)
        examined = f"{n} path{'' if n == 1 else 's'} examined"
        # 🔴 The zero is described by WHAT PRODUCED IT, never by one sentence
        # covering every case. An earlier revision printed "none named an entry"
        # unconditionally and was FALSE in the live smoke run: an entry HAD been
        # named and had merely stayed under `min_paths`. A wrong explanation of
        # a zero is worse than the bare zero — it forecloses the next question.
        if report.below_threshold:
            out.append(
                f"NOTHING CLEARED THE THRESHOLD — {examined}; "
                f"{len(report.below_threshold)} entr"
                f"{'y was' if len(report.below_threshold) == 1 else 'ies were'} named but "
                f"stayed under min_paths={report.min_paths} (listed above). Entries WERE "
                f"reached; none strongly enough."
            )
        elif report.status == "no-match":
            out.append(
                f"NOTHING RESOLVED — {examined} and none named an entry in "
                f"`{report.scope}`, and none clustered enough to nominate one. This is a "
                f"real zero, not an empty window."
            )
        else:
            out.append(f"NOTHING TO PROPOSE — {examined}; see the accounting above.")
        out.append("Propose no write.")

    return "\n".join(out)


def report_json(report: TouchReport) -> dict:
    src = report.source
    assoc = report.association
    return {
        "status": report.status,
        "scope": report.scope,
        "store_root": report.store_root,
        "today": report.today,
        "min_paths": report.min_paths,
        "writer_id": WRITER_ID,
        "source": {
            "kind": src.kind,
            "window": src.window,
            "base_ref": src.base_ref,
            "caveat": src.caveat,
            "notes": list(src.notes),
            "commands": [list(c) for c in src.commands],
            "path_count": len(src.paths),
            "paths": list(src.paths),
        },
        "known": [
            {
                "ref": m.entry.ref,
                "file": report.entry_files.get(m.entry.ref, m.entry.filename),
                "path_count": m.path_count,
                "paths": list(m.paths),
                "evidence": [
                    {
                        "path": e.path,
                        "component": e.component,
                        "ref": e.ref,
                        "tier": e.tier,
                        "matched_alias": e.matched_alias,
                    }
                    for e in m.evidence
                ],
            }
            for m in report.known
        ],
        "below_threshold": [
            {"ref": m.entry.ref, "path_count": m.path_count} for m in report.below_threshold
        ],
        "ambiguous": [
            {
                "ref": a.ref,
                "tier": a.tier,
                "candidates": list(a.candidates),
                "paths": list(a.paths),
            }
            for a in report.ambiguous
        ],
        "nominations": [
            {
                "ref": n.ref,
                "path_count": n.path_count,
                "depth": n.depth,
                "coherent": n.coherent,
                "fans_out": n.fans_out,
                "paths": list(n.paths),
            }
            for n in report.nominations
        ],
        "unmatched_path_count": len(assoc.unmatched_paths) if assoc else 0,
        "journal_line_shape": journal_line_shape(report.today),
    }


# --- The census: what makes the experiment falsifiable -------------------------


@dataclass(frozen=True)
class Census:
    """Counts that answer "did entries accrue outside infra recon?".

    The decision doc's reopening gate is a COUNT ("≥5 entries outside its
    current single scope"), so the instrument for it must be a count too — not a
    recollection of which sessions felt productive. This reads the store
    read-only and reports the raw numbers; it deliberately renders no verdict,
    because the threshold lives in the decision doc and restating it here would
    be the same number in two places.
    """

    total: int
    by_scope: Mapping[str, int]
    by_writer: Mapping[str, int]
    scopes_with_stamped_entries: Mapping[str, Mapping[str, int]]

    def to_json(self) -> dict:
        return {
            "total": self.total,
            "by_scope": dict(self.by_scope),
            "by_writer": dict(self.by_writer),
            "by_scope_and_writer": {k: dict(v) for k, v in self.scopes_with_stamped_entries.items()},
        }


def census(store_root: str | Path) -> Census:
    """Count entries by scope and by `created_by:`. READ-ONLY.

    Front matter is parsed with the resolver's own `parse_front_matter`, not a
    second parser: the live corpus uses an inline flow list and hand-rolled
    quoting that PyYAML would type-coerce, and two parsers over one file format
    is the duplicated-predicate shape again.
    """
    store = Path(store_root)
    if not store.is_dir():
        raise StoreMissingError(f"store root not found: {store}")
    by_scope: dict[str, int] = {}
    by_writer: dict[str, int] = {}
    nested: dict[str, dict[str, int]] = {}
    for scope_dir in sorted(p for p in store.iterdir() if p.is_dir()):
        scope = scope_dir.name
        by_scope.setdefault(scope, 0)
        nested.setdefault(scope, {})
        for md in sorted(scope_dir.glob("*.md")):
            if md.name == "README.md":
                continue
            fm = parse_front_matter(md.read_text(encoding="utf-8", errors="replace"))
            raw = fm.get("created_by")
            writer = raw.strip() if isinstance(raw, str) and raw.strip() else UNSTAMPED
            by_scope[scope] += 1
            by_writer[writer] = by_writer.get(writer, 0) + 1
            nested[scope][writer] = nested[scope].get(writer, 0) + 1
    return Census(
        total=sum(by_scope.values()),
        by_scope=dict(sorted(by_scope.items())),
        by_writer=dict(sorted(by_writer.items())),
        scopes_with_stamped_entries={k: dict(sorted(v.items())) for k, v in sorted(nested.items())},
    )


def render_census(c: Census) -> str:
    out = [f"subsystem-touch census: {c.total} entries"]
    out.append("  by scope:")
    for scope, n in c.by_scope.items():
        out.append(f"    {scope}: {n}")
    out.append("  by created_by:")
    for writer, n in c.by_writer.items():
        out.append(f"    {writer}: {n}")
    out.append(
        "  (the reopening gate is stated in "
        "claudedocs/decision-subsystem-store-rejected-2026-08-11.md, not here)"
    )
    return "\n".join(out)


# --- CLI -----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="subsystem-touch",
        description=(
            "Report which /analyze-service index entries this session's changed paths "
            "touch. READ-ONLY: it never writes to the store."
        ),
    )
    p.add_argument("--repo", default=".", help="repo to read paths from (default: cwd)")
    p.add_argument("--scope", default=None, help="override the derived store scope")
    p.add_argument("--store", default=str(DEFAULT_STORE_ROOT), help="store root")
    p.add_argument(
        "--paths-from",
        default="git",
        help="`git` (default) or `-` to read repo-relative paths from stdin, one per line",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="REPO_REL_PATH",
        help=(
            "repo-relative path to drop from the git window (repeatable). For the "
            "ritual's own artifact: /handoff writes its doc, then asks what changed, "
            "so without this the doc's own directory is a nomination on every run."
        ),
    )
    p.add_argument("--min-paths", type=int, default=DEFAULT_MIN_PATHS)
    p.add_argument("--limit", type=int, default=DEFAULT_NOMINATION_LIMIT)
    p.add_argument("--today", default=None, help="YYYY-MM-DD for the journal shape")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--census",
        action="store_true",
        help="count entries by scope and created_by, and exit (the falsifiability instrument)",
    )
    p.add_argument(
        "--template",
        metavar="SLUG",
        default=None,
        help="print the minimal new-entry template for SLUG and exit",
    )
    return p


def main(argv: Sequence[str] | None = None, *, today: str | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    from datetime import date  # local: the pure layer above never imports a clock

    stamp = args.today or today or date.today().isoformat()

    try:
        if args.census:
            c = census(args.store)
            print(json.dumps(c.to_json(), indent=2) if args.json else render_census(c))
            return 0

        repo = Path(args.repo).resolve()
        scope = args.scope
        if scope is None:
            common = _git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"]).strip()
            top = _git(repo, ["rev-parse", "--show-toplevel"]).strip()
            scope = derive_scope(top, common)

        if args.template is not None:
            print(new_entry_template(normalize_ref(args.template), scope, today=stamp))
            return 0

        if args.paths_from == "git":
            source = collect_git_paths(repo, exclude=args.exclude)
        elif args.paths_from == "-":
            source = caller_supplied(sys.stdin.read().splitlines())
        else:
            print("subsystem-touch: --paths-from must be `git` or `-`", file=sys.stderr)
            return 2

        report = build_report(
            source,
            args.store,
            scope,
            today=stamp,
            min_paths=args.min_paths,
            limit=args.limit,
        )
    except (TouchError, ResolverError) as exc:
        print(f"subsystem-touch: {exc}", file=sys.stderr)
        return 3

    print(json.dumps(report_json(report), indent=2) if args.json else render_text(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
