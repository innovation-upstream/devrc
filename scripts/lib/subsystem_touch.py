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
Two real sources, in preference order: the SESSION's own transcript, falling
back to GIT. The reporting core takes paths as an argument and has never heard
of either, which is what let the second one be added without touching the
matching logic.

🔴 GIT WAS THE ORIGINAL SOURCE AND IS STRUCTURALLY BLIND TO THE BEST SESSIONS.
On its first real invocation this tool captured NOTHING from the session it was
built during, and the reason is not a bug: that session landed all its work
through merged PRs, so by `/handoff` time `git diff HEAD` was empty and HEAD sat
at the merge-base. The window was honestly, uselessly empty — and the tool said
so, which is not the same as being useful. Merging as you go is the BETTER
working pattern, so the source that cannot see it is the one that has to change.

  * SESSION TRANSCRIPT (`--session <uuid>` / `--transcript <path>`) — PREFERRED.
    Per-session by construction, and independent of git: it records the edit
    whether or not the commit survived, merged, or left a branch behind. The
    extraction is REUSED, not rewritten — `scripts/collector/claude/
    session-tailer.py` plus `scripts/collector/changed_paths.py` already compute
    exactly this set for `kind=session-summary`, tested and deployed. Its blind
    spots are real and are printed in the caveat, not buried here: a SUBAGENT's
    edits (a separate transcript — measured at 196 of 733 file-tool calls across
    the 40 most recent transcripts), files written by a Bash command rather than
    a file tool, and paths outside the session cwd (counted, never dropped).
  * GIT (`--paths-from git`, the default) — FALLBACK. Deterministic, re-runnable,
    already built and tested, and honest about a window that is bounded to the
    BRANCH rather than the session:

        (working tree vs HEAD, incl. untracked)  ∪  (merge-base(HEAD, base)..HEAD)

    On a branch that IS the base the commit half is empty and the window
    degrades to `worktree` — SAID, not silently emptied.
  * THE AGENT'S OWN MEMORY of what it edited was rejected outright, and still is:
    it cannot be re-run to the same answer and cannot be tested, which is the
    "prose/heuristic" fix `claude/RULES.md` says to prefer a structural one over.

🔴 EACH SOURCE PRINTS ITS OWN CAVEAT, AND THE OLD ONE IS NOT REUSABLE. The git
caveat's "what this BRANCH touched, NOT what this SESSION touched" is false in
the OTHER direction once the source is per-session — it would understate a
window that is exactly what it disclaims. `PathSource.caveat` branches per
source; every renderer prints it, on every output path.

🔴 A SESSION ID IS VALIDATED, NOT TRUSTED, AND A FAILURE NEVER FALLS BACK TO GIT.
There is no session-id environment variable, so the id arrives as an argument
and can name another session — and "newest transcript by mtime" is not a
fallback but a coin flip (14 transcripts modified within one 5-minute window,
measured). Falling back to git on a validation failure would answer a question
the caller did not ask, using a window that overlaps enough to look right. See
`collect_session_paths` for the four guards and the order reachability forces
them into.


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
    collect_session_paths(repo, session=…)    -> PathSource      (PREFERRED)
    find_transcript(session)                  -> Path
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

and, for the session source — each FATAL, none falling back to git:

    "session path extractor not found"  ExtractorMissingError
    "transcript not found"              TranscriptMissingError
    "transcript id is ambiguous"        TranscriptAmbiguousError
    "transcript is stale"               TranscriptStaleError
    "transcript unreadable"             TranscriptUnreadableError
    "transcript cwd does not match"     TranscriptCwdMismatchError

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
import time
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
    "MAX_TRANSCRIPT_AGE_SECONDS",
    "TouchError",
    "StoreMissingError",
    "GitError",
    "ExtractorMissingError",
    "TranscriptMissingError",
    "TranscriptAmbiguousError",
    "TranscriptStaleError",
    "TranscriptUnreadableError",
    "TranscriptCwdMismatchError",
    "PathSource",
    "Nomination",
    "TouchReport",
    "Census",
    "derive_scope",
    "collect_git_paths",
    "collect_session_paths",
    "find_transcript",
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

# 🔴 THE LIVENESS BOUND ON A CALLER-SUPPLIED SESSION ID. There is no session-id
# environment variable, so the id arrives as an argument and can be WRONG — a
# uuid copied out of an old handoff doc, or a resumed session's. "Newest by
# mtime" is not a usable fallback either: MEASURED 2026-08-12 over the live
# transcript corpus (4,873 files, read-only), 14 were modified within the same
# 5 minutes. So the id is trusted only after the file it names is shown to be
# LIVE.
#
# 30 minutes, chosen from that same measurement — transcripts within each bound,
# of 4,873:
#
#     5m  14      15m  19      30m  25      1h  36      2h  38      6h  50
#     24h 210
#
# i.e. 30m admits 0.5% of the corpus and rejects 4,848 files, while 24h would
# admit 8.4x as many. The lower bound comes from the other direction: `/handoff`
# appends to this transcript on every turn, and step 4 runs one assistant turn
# after step 3 wrote the doc — seconds to low minutes. 30m is ~1.5 orders of
# magnitude of headroom over that and still rejects essentially the whole
# corpus, which is the property that matters: the check must survive a slow turn
# and still fail a yesterday's-uuid paste.
#
# 🔴 DELIBERATELY NOT env-overridable and NOT a CLI flag. A safety bound with an
# escape hatch is a bound the first inconvenienced caller removes. Tests control
# it by setting the FIXTURE's mtime, never by widening the constant.
MAX_TRANSCRIPT_AGE_SECONDS = 1800.0

_SENSITIVITY_FAIL_SAFE = "client-confidential"


# --- Errors --------------------------------------------------------------------


class TouchError(Exception):
    """Base for every error this module raises."""


class StoreMissingError(TouchError):
    """The store root does not exist. Sentinel: 'store root not found'."""


class GitError(TouchError):
    """A git invocation failed. Sentinel: 'git command failed'."""


# --- Session-source errors -----------------------------------------------------
#
# 🔴 EVERY ONE OF THESE IS FATAL, AND NONE FALLS BACK TO GIT. A caller that asked
# "what did SESSION x touch?" and silently got "what this BRANCH touched" has a
# plausible answer to a question it did not ask — the confident-wrong-answer
# class this whole module exists to avoid, made worse because the two windows
# genuinely overlap so the answer looks right. `/handoff` step 4 already treats
# any non-zero exit as "write nothing", so failing is CHEAPER than guessing.
#
# Each carries its own sentinel phrase, so a caller — or a mutation test — can
# tell WHICH guard fired. No two share a spelling.


class ExtractorMissingError(TouchError):
    """The shared transcript extractor is not on disk.

    Sentinel: 'session path extractor not found'. A broken deploy, not a reading
    — and a live hazard in THIS repo specifically, where a new file that was
    never `git add`ed is silently omitted from the flake's deploy.
    """


class TranscriptMissingError(TouchError):
    """No transcript for the given session id. Sentinel: 'transcript not found'."""


class TranscriptAmbiguousError(TouchError):
    """One id resolved to more than one transcript.

    Sentinel: 'transcript id is ambiguous'. Never observed in the live corpus
    (0 duplicate stems across 4,873 files, measured 2026-08-12) — but a resolver
    that PICKS when it cannot tell is how a session's paths get attributed to
    another session, so it refuses and names the candidates.
    """


class TranscriptStaleError(TouchError):
    """The transcript's mtime is older than the liveness bound.

    Sentinel: 'transcript is stale'. This is the guard that catches the wrong
    argument — see `MAX_TRANSCRIPT_AGE_SECONDS` for the bound and its
    measurement.
    """


class TranscriptUnreadableError(TouchError):
    """The transcript could not be read as a session at all.

    Sentinel: 'transcript unreadable'. Distinct from "this session changed no
    files": the extractor reports an unobservable file set as None and an
    observed-empty one as [], and conflating the two is exactly the defect the
    shared module was built to prevent.
    """


class TranscriptCwdMismatchError(TouchError):
    """The transcript's recorded cwd is not the repo under test.

    Sentinel: 'transcript cwd does not match'. Not a formality: the extractor
    relativizes every path against the SESSION's cwd, so paths from a session
    rooted elsewhere are repo-relative to the WRONG repo — they would resolve,
    quietly, against this repo's index.
    """


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
    """`git`, `session` or `caller`."""

    window: str
    """`branch` (worktree ∪ this branch's commits), `worktree`, `session`, or `supplied`."""

    paths: tuple[str, ...]
    base_ref: str | None = None
    commands: tuple[tuple[str, ...], ...] = ()
    notes: tuple[str, ...] = ()
    session: str | None = None
    """The session id, when `kind == "session"`. Part of the caveat, not decoration."""

    @property
    def caveat(self) -> str:
        """The claim this source can actually support. Never widened by a caller.

        🔴 ONE CAVEAT PER SOURCE, AND EACH UNDERSTATES IN ITS OWN DIRECTION. The
        git caveat's "NOT what this SESSION touched" is not a disclaimer that can
        be reused: pointed at the session source it would be false in the
        opposite direction — understating a window that IS per-session. A single
        hedging sentence covering both would be wrong about both, so each branch
        names the work its own window structurally cannot see.
        """
        if self.kind == "caller":
            return "paths were supplied by the caller; provenance is the caller's to state"
        if self.kind == "session":
            return (
                f"session transcript {self.session}: the files THIS SESSION's own turns "
                f"edited (Edit/Write/NotebookEdit/MultiEdit), relative to the session cwd "
                f"— independent of git, so work already merged or committed still counts. "
                f"NOT represented: (a) anything a SUBAGENT edited — its turns are excluded "
                f"as a separate session, and 196 of 733 file-tool calls across the 40 most "
                f"recent transcripts were a subagent's (measured 2026-08-12); (b) files "
                f"written by a Bash command rather than a file tool; (c) paths outside the "
                f"session cwd, counted in the note above"
            )
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


def _filter_excluded(
    paths: Iterable[str], exclude: Iterable[str]
) -> tuple[list[str], list[str]]:
    """(kept, dropped) — dedupe in first-seen order, minus the caller's exclusions.

    🔴 ONE predicate for BOTH real sources. `/handoff` writes its own doc in step
    2 and asks what changed in step 4, so the doc is in the window either way —
    it is untracked in git's, and it is a `Write` tool call in the session's. An
    exclusion honoured by one source and not the other would make the ritual's
    own artifact a nomination on exactly half the runs, which is the
    duplicated-predicate shape `claude/RULES.md` says regenerates the same bug at
    every site.

    Dropped paths are RETURNED, never discarded: the caller counts them into
    `notes`, because a window that silently shrank is indistinguishable from one
    that was always small.
    """
    excluded = {e.strip() for e in exclude if e and e.strip()}
    kept: list[str] = []
    dropped: list[str] = []
    for p in paths:
        if p in excluded:
            if p not in dropped:
                dropped.append(p)
        elif p not in kept:
            kept.append(p)
    return kept, dropped


def _exclusion_note(dropped: Sequence[str]) -> str:
    return (
        f"excluded {len(dropped)} caller-named path(s) from the window: "
        f"{', '.join(dropped)}"
    )


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
    commands: list[tuple[str, ...]] = []
    notes: list[str] = []
    raw: list[str] = []

    def add(new: Iterable[str]) -> None:
        raw.extend(new)

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

    # Filtered ONCE, at the end, over the accumulated window: `_filter_excluded`
    # dedupes in first-seen order, so this is the same set and the same order the
    # per-batch filter produced, from one predicate the session source shares.
    paths, dropped = _filter_excluded(raw, exclude)
    if dropped:
        notes.append(_exclusion_note(dropped))

    return PathSource(
        kind="git",
        window=window,
        paths=tuple(paths),
        base_ref=base_ref,
        commands=tuple(commands),
        notes=tuple(notes),
    )


# --- The session source: paths from the session's OWN transcript ---------------
#
# 🔴 THE EXTRACTOR IS REUSED, NOT REWRITTEN. `scripts/collector/claude/
# session-tailer.py` already turns a transcript into a changed-path set — it is
# how `changed_paths` on `kind=session-summary` is computed — and
# `scripts/collector/changed_paths.py` already owns the relativization, the
# dedupe, the ordering and the cap. Both are tested and deployed on both hosts.
# A second extractor here would be the duplicated predicate `claude/RULES.md`
# names, and the first one has ALREADY drifted once in exactly that way: the
# opencode summariser read key names its store never used and emitted
# `files_modified=0` for every session for months, green against fixtures built
# in the same wrong shape. So this file supplies a path and a repo, and takes
# back the answer.
#
# The tailer's filename contains a dash, so it cannot be imported by name; it is
# loaded by explicit importlib path, the idiom already used for it in
# `scripts/collector/claude/tests/test_session_tailer.py`.


def _session_tailer_path() -> Path:
    """`scripts/collector/claude/session-tailer.py`, from this file's location.

    Same `.resolve()` idiom as this module's own `sys.path` line above — and
    unlike the collector's internal imports, safe here: `scripts/lib/` is not a
    `home.file` target, so this path never runs from `/nix/store`.
    """
    return Path(__file__).resolve().parent.parent / "collector" / "claude" / "session-tailer.py"


_SESSION_TAILER = None


def _session_tailer():
    """Load the shared tailer once. LAZY on purpose.

    Importing it drags in `changed_paths`, `_shared` and `tailer` and mutates
    `sys.path`; the git and caller sources must not pay that, and — more to the
    point — `TestMutationKillMatrix` exec's copies of THIS module, so a heavy
    module-level import chain would be re-run for every mutant.
    """
    global _SESSION_TAILER
    if _SESSION_TAILER is not None:
        return _SESSION_TAILER

    import importlib.util

    mod_path = _session_tailer_path()
    if not mod_path.is_file():
        raise ExtractorMissingError(
            f"session path extractor not found: {mod_path} — the transcript source "
            f"cannot run without it. In this repo that usually means a file was "
            f"never `git add`ed and the flake omitted it from the deploy."
        )
    # The tailer imports its siblings (`_shared`, `tailer`) by BARE NAME and only
    # puts its own PARENT on sys.path, because it is normally run as a script
    # from its own directory. Loaded from here, that directory is not on the path
    # and those imports would fail — so both go on explicitly.
    for d in (str(mod_path.parent.parent), str(mod_path.parent)):
        if d not in sys.path:
            sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("devrc_session_tailer", mod_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ExtractorMissingError(
            f"session path extractor not found: {mod_path} could not be loaded as a module"
        )
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: the tailer defines dataclasses, and `@dataclass`
    # resolves string annotations through `sys.modules[__module__]`.
    sys.modules["devrc_session_tailer"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("devrc_session_tailer", None)
        raise
    _SESSION_TAILER = module
    return module


def find_transcript(session: str, roots: Sequence[str] | None = None) -> Path:
    """Resolve a session id to its ONE transcript file. Never guesses.

    Roots come from the collector's own `projects_roots()` — one definition of
    where transcripts live, and it honours `CLAUDE_PROJECTS_DIR` so a test can
    point the lookup at a fixture tree instead of the real one.

    ⚠ DELIBERATELY DIVERGENT from the tailer's `iter_transcripts`, which skips
    `subagents/` and `wf_*` directories. That exclusion exists so the telemetry
    walker does not summarise a subagent's turns twice, under both the parent and
    itself. Here exactly one id is being resolved, so there is no double count to
    avoid — and a subagent that runs `/handoff` in its own worktree should get
    ITS transcript, not a miss. Measured 2026-08-12: 4,254 of 4,873 transcripts
    live under `subagents/`, and 0 stems appear in more than one directory.
    """
    sid = (session or "").strip()
    # A path separator or a leading dot would make the glob below escape the
    # roots entirely. Rejected as unusable rather than searched for.
    if not sid or "/" in sid or os.sep in sid or sid.startswith("."):
        raise TranscriptMissingError(
            f"transcript not found: {session!r} is not a usable session id — pass the "
            f"session UUID, which is the basename of the agent scratchpad directory"
        )
    if roots is None:
        roots = _session_tailer().projects_roots()
    hits: list[Path] = []
    for root in roots:
        for p in sorted(Path(root).glob(f"**/{sid}.jsonl")):
            if p not in hits:
                hits.append(p)
    if not hits:
        raise TranscriptMissingError(
            f"transcript not found: no `{sid}.jsonl` under {', '.join(str(r) for r in roots)} "
            f"— the session id is wrong, or this session's transcript is stored elsewhere"
        )
    if len(hits) > 1:
        raise TranscriptAmbiguousError(
            f"transcript id is ambiguous: `{sid}` resolves to {len(hits)} files "
            f"({', '.join(str(h) for h in hits)}); pass --transcript to name one"
        )
    return hits[0]


def _same_dir(a: str, b: str) -> bool:
    """Do two directory strings name the same directory?

    Lexical equality first, `realpath` second. The lexical pass is what the
    extractor itself uses to relativize (deliberately not `realpath`, so a
    historical session whose cwd no longer exists still resolves); the realpath
    pass exists only for the symlinked-`$HOME` case, where git's
    `--show-toplevel` and a transcript's recorded cwd spell one directory two
    ways. Empty is never equal to anything — `realpath("")` is the process cwd,
    which would make an unreadable session's empty cwd "match" whatever
    directory the tool happened to be launched from.
    """
    if not a or not b:
        return False
    na = os.path.normpath(a).rstrip("/") or "/"
    nb = os.path.normpath(b).rstrip("/") or "/"
    if na == nb:
        return True
    return os.path.realpath(na) == os.path.realpath(nb)


def collect_session_paths(
    repo: str | Path,
    *,
    session: str | None = None,
    transcript: str | Path | None = None,
    roots: Sequence[str] | None = None,
    now: float | None = None,
    max_age_seconds: float = MAX_TRANSCRIPT_AGE_SECONDS,
    exclude: Iterable[str] = (),
) -> PathSource:
    """What THIS SESSION touched, from its own transcript. Per-session, not per-branch.

    The window git cannot express. `/handoff` fires at the end of a session, and
    the sessions worth recording are the ones that landed their work through
    merged PRs — by then `git diff HEAD` is empty and HEAD sits at the
    merge-base, so the git window is honestly, uselessly, empty. The transcript
    is not: it records the edit whether or not the commit survived, whether or
    not it merged, and whether or not the branch still exists.

    🔴 THE ID IS VALIDATED, NEVER TRUSTED, AND A FAILURE NEVER FALLS BACK. There
    is no session-id environment variable, so this arrives as an argument and can
    name someone else's session. Four guards, in an order where each is
    reachable by an input no earlier one rejects:

      1. the transcript RESOLVES        TranscriptMissingError / ...Ambiguous
      2. its mtime is LIVE              TranscriptStaleError
      3. it READS as a session          TranscriptUnreadableError
      4. its cwd IS this repo           TranscriptCwdMismatchError

    (3) must precede (4): an unreadable transcript yields `cwd == ""`, so a cwd
    check placed first would fire for it and the unreadable case's own test would
    pass on a neighbour's error — green with the guard it claims to test deleted.

    Falling back to git on any of these would answer a question the caller did
    not ask, with a window that overlaps enough to look right.

    `now` and `max_age_seconds` are parameters so the liveness check is testable
    without a clock; neither is reachable from the CLI, deliberately.
    """
    tailer = _session_tailer()
    if transcript is not None:
        path = Path(transcript)
        if not path.is_file():
            raise TranscriptMissingError(
                f"transcript not found: {path} is not a readable file"
            )
    elif session is not None:
        path = find_transcript(session, roots)
    else:  # pragma: no cover - the CLI and every caller pass exactly one
        raise TranscriptMissingError(
            "transcript not found: neither a session id nor a transcript path was given"
        )
    label = session or path.stem

    # --- guard 1b: readable, not merely present ---------------------------------
    if not os.access(path, os.R_OK):
        raise TranscriptMissingError(
            f"transcript not found: {path} exists but is not readable"
        )

    # --- guard 2: the file is LIVE ----------------------------------------------
    stamp = now if now is not None else time.time()
    age = stamp - os.stat(path).st_mtime
    if age > max_age_seconds:
        raise TranscriptStaleError(
            f"transcript is stale: {path} was last written {age / 60:.1f} min ago, "
            f"bound is {max_age_seconds / 60:.0f} min — this is not the session that "
            f"is running now, so its paths are another session's. Pass the CURRENT "
            f"session id (the basename of the agent scratchpad directory)."
        )

    # --- guard 3: it reads as a session -----------------------------------------
    # 🔴 The shared extractor. A trailing line that is half-written — the normal
    # state of a transcript being appended to while it is read — is skipped by
    # its per-line JSON decode, so a partial write cannot crash the run; and a
    # transcript it could not read at all comes back with `changed_paths = None`
    # (the "unobservable" block), NEVER with `[]`. Those two are checked
    # together here because they are one claim: we do not know this session's
    # file set. `[]` is a different fact — the session touched nothing — and it
    # flows through as `looked-at-nothing`.
    #
    # ⚠ `unreadable` is REDUNDANT-BUT-KEPT, labelled so a mutation sweep does not
    # re-derive it as a live guard: the extractor's `_empty_rollup` already
    # carries the all-None block, and `build_rollup` calls `_mark_unobservable`
    # whenever it sets `unreadable`, so today `unreadable` IMPLIES
    # `changed_paths is None` and removing this clause alone is unkillable. It
    # stays because the two are separate claims in the extractor's own contract,
    # and an extractor that ever reported one without the other would otherwise
    # be believed.
    rollup = tailer.summarize_transcript(str(path))
    observed = rollup.get("changed_paths")
    if rollup.get("unreadable") or observed is None:
        raise TranscriptUnreadableError(
            f"transcript unreadable: {path} yielded no readable session — the file set "
            f"is UNKNOWN, which is not the same as empty, so nothing is reported rather "
            f"than an empty window that reads as 'this session touched nothing'"
        )

    # --- guard 4: it is THIS repo ------------------------------------------------
    # The frame, resolved exactly as the git source resolves it: every path the
    # extractor emits is relative to the session cwd, so unless that cwd IS the
    # repo root, the paths are repo-relative to a different repo and would
    # resolve — silently, plausibly — against this repo's index.
    toplevel = _git(Path(repo), ["rev-parse", "--show-toplevel"]).strip()
    session_cwd = rollup.get("cwd") or ""
    if not _same_dir(session_cwd, toplevel):
        raise TranscriptCwdMismatchError(
            f"transcript cwd does not match: session {label} ran in "
            f"{session_cwd or '(no cwd recorded)'}, but --repo resolves to {toplevel}. "
            f"Every path in a transcript is relative to the session's own cwd, so "
            f"reporting them against this repo would associate another repo's work here."
        )

    paths, dropped = _filter_excluded(observed, exclude)
    notes: list[str] = []
    total = rollup.get("changed_paths_total")
    outside = rollup.get("changed_paths_outside_cwd")
    # 🔴 EMITTED UNCONDITIONALLY, INCLUDING AT ZERO. The caveat below refers to
    # this count, so it has to be there to refer to — and a stated zero is a
    # reading, while an absent line is indistinguishable from a counter wired to
    # nothing (`claude/RULES.md` → "report the pair").
    notes.append(
        f"{total} distinct path(s) under the session cwd; {outside} outside it — the "
        f"latter are COUNTED here and not represented below (a scratchpad file, a "
        f"temp worktree, or an edit in another repo has no repo-relative form here)"
    )
    if rollup.get("changed_paths_truncated"):
        notes.append(
            f"🔴 TRUNCATED at the extractor's cap of {rollup.get('changed_paths_cap')} — "
            f"the list is a lexicographic PREFIX of {total} paths, so a late-sorting "
            f"subtree may be missing entirely"
        )
    if dropped:
        notes.append(_exclusion_note(dropped))
    notes.append(f"transcript: {path}")

    return PathSource(
        kind="session",
        window="session",
        paths=tuple(paths),
        commands=(("rev-parse", "--show-toplevel"),),
        notes=tuple(notes),
        session=label,
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
            "session": src.session,
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
        help=(
            "fallback source when no session is given: `git` (default) or `-` to read "
            "repo-relative paths from stdin, one per line"
        ),
    )
    # 🔴 MUTUALLY EXCLUSIVE, ENFORCED BY ARGPARSE (exit 2). They name the same
    # thing two ways, and a caller that passes both has one of them wrong —
    # picking either would be a guess about which.
    session_src = p.add_mutually_exclusive_group()
    session_src.add_argument(
        "--session",
        default=None,
        metavar="UUID",
        help=(
            "PREFERRED source: report what THIS SESSION touched, from its own "
            "transcript. The UUID is the basename of the agent scratchpad directory. "
            "Validated, never trusted — a wrong or stale id FAILS rather than falling "
            "back to git."
        ),
    )
    session_src.add_argument(
        "--transcript",
        default=None,
        metavar="PATH",
        help=(
            "the session source, naming the transcript file directly instead of "
            "resolving a UUID. Same validation; use it when --session reports the id "
            "is ambiguous."
        ),
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

        wants_session = args.session is not None or args.transcript is not None
        # 🔴 A CONTRADICTION IS REFUSED, NOT RESOLVED. `--paths-from` names the
        # FALLBACK source, so pairing it with a session request asks for two
        # different windows at once. Honouring either silently would hand back a
        # plausible answer to a question the caller did not ask — the same
        # failure the no-fallback rule above exists to prevent, arriving through
        # argument parsing instead.
        if wants_session and args.paths_from != "git":
            print(
                "subsystem-touch: --session/--transcript cannot be combined with "
                "--paths-from; they are different path windows. Drop one.",
                file=sys.stderr,
            )
            return 2

        if wants_session:
            source = collect_session_paths(
                repo,
                session=args.session,
                transcript=args.transcript,
                exclude=args.exclude,
            )
        elif args.paths_from == "git":
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
