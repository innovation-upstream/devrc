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
Three real sources: the SESSION's own transcript, the PULL REQUESTS a branch
landed, and GIT. The reporting core takes paths as an argument and has never
heard of any of them, which is what let the second and third be added without
touching the matching logic.

🔴 THEY ANSWER TWO DIFFERENT QUESTIONS, AND THE DIFFERENCE IS NOT A NUANCE.
`--session` answers "what did THIS SESSION touch". `--pr` answers "what did THIS
BRANCH LAND". Git's window is a third thing again (this branch's uncommitted +
committed work, whoever authored it). They are not three approximations of one
quantity — a PR's file list is the UNION of every commit on its branch, so it
contains another session's commits, a subagent's, and hand-made ones, while a
session transcript contains exactly one session's turns and nobody else's. Each
`caveat` says which question its own window answers; see `PathSource.caveat`.

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
  * PULL REQUESTS (`--pr <n>[,<n>...]`) — the only source that sees a SUBAGENT's
    work. The standing default in this environment is to DELEGATE non-trivial
    work to a subagent, and a subagent's turns are a separate transcript the
    session source excludes by construction (196 of 733 file-tool calls,
    measured) — so on exactly the sessions worth recording, the session window
    is thin and the implementation is invisible. `gh pr view <n> --json files`
    is immune to all three blind spots at once: it does not care which session,
    which agent, or which tool wrote the bytes, and merged work still counts.
    🔴 What it buys in coverage it pays for in ATTRIBUTION: it over-reports in
    the exact direction the session source under-reports, and it cannot tell you
    which. That is why it is a separate flag with a separate caveat rather than
    a widening of `--session`, and why the two do not compose (see below).
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

🔴 THE SOURCES DO NOT COMPOSE — ONE QUESTION PER RUN, ENFORCED BY ARGPARSE.
`--session`, `--transcript`, `--pr` and `--paths-from` are mutually exclusive.
Unioning `--session` with `--pr` was considered and REJECTED, and the reason is
the caveat rather than the paths:

  * A union has ONE `caveat` line describing a set assembled from two windows
    with opposite biases. It would have to assert session attribution for some
    members and deny it for others, in one sentence, with no way for a reader to
    tell which is which. This module's own rule — "a single hedging sentence
    covering both sources would be wrong about both" — already forbids that
    shape for two sources; a union makes it unavoidable rather than optional.
  * The consumer's decision is per-path. `/handoff` proposes a dated journal
    bullet against a curated, client-confidential, unbacked-up store. "This
    session worked on X" and "some session moved X on this branch" are different
    claims and only one of them belongs in a work-history bullet. A merged set
    destroys exactly the fact needed to choose.
  * It matches the refusal already enforced for `--session` + `--paths-from`,
    which is the SAME shape: two windows, one answer. Composing here while
    refusing there would leave the module inconsistent about its own principle.

The composition that IS available is honest and needs no code: RUN IT TWICE,
once per source, and read two reports each carrying its own caveat. That keeps
attribution intact, which the union destroys. `/handoff` step 4 does exactly
that.

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
    collect_pr_paths(repo, numbers, fetch=…)  -> PathSource      (runs gh)
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

and, for the PR source — the first source that leaves this machine, so the first
with an ENVIRONMENTAL failure surface. Each is FATAL, none falls back, and none
of them can return an empty path set:

    "repo has no usable github remote"         RepoRemoteError
    "gh cli not found"                         GhMissingError
    "gh is not authenticated"                  GhAuthError
    "github api rate limit"                    GhRateLimitError
    "github api call failed"                   GhApiError   (the FALLBACK: any
                                               unrecognised gh failure, network
                                               included, lands here rather than
                                               being mapped to something specific)
    "pull request not found"                   PrNotFoundError
    "pull request belongs to another repository"  PrRepoMismatchError
    "pull request is closed unmerged"          PrNotLandedError
    "pull request response is malformed"       PrResponseMalformedError
    "pull request file list is truncated"      PrFileListTruncatedError

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
import collections.abc as _abc
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
    NUANCE_HEADING,
    AmbiguousRefError,
    Association,
    EntryUnreadableError,
    JournalBullet,
    MalformedEntryError,
    ResolverError,
    SubsystemEntry,
    SubsystemIndex,
    UnknownScopeError,
    associate_paths,
    extract_sections,
    load_index,
    normalize_ref,
    parse_front_matter,
    parse_journal_bullets,
    path_refs,
    resolve_ref_tiered,
)

__all__ = [
    "WRITER_ID",
    "DEFAULT_STORE_ROOT",
    "DEFAULT_NOMINATION_LIMIT",
    "BASE_REF_CANDIDATES",
    "MAX_TRANSCRIPT_AGE_SECONDS",
    "JOURNAL_BULLETS_SHOWN",
    "JOURNAL_BULLET_MAX_LINES",
    "TouchError",
    "StoreMissingError",
    "EntryUnreadableError",
    "GitError",
    "ExtractorMissingError",
    "TranscriptMissingError",
    "TranscriptAmbiguousError",
    "TranscriptStaleError",
    "TranscriptUnreadableError",
    "TranscriptCwdMismatchError",
    "RepoRemoteError",
    "GhMissingError",
    "GhAuthError",
    "GhRateLimitError",
    "GhApiError",
    "PrNotFoundError",
    "PrRepoMismatchError",
    "PrNotLandedError",
    "PrResponseMalformedError",
    "PrFileListTruncatedError",
    "PR_JSON_FIELDS",
    "PR_ACCEPTED_STATES",
    "PathSource",
    "Nomination",
    "EntryJournal",
    "read_entry_journal",
    "TouchReport",
    "Census",
    "derive_scope",
    "scope_for_repo",
    "collect_git_paths",
    "collect_session_paths",
    "collect_pr_paths",
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

# 🔴 HOW MANY EXISTING BULLETS THE KNOWN-ENTRIES BLOCK SHOWS BEFORE PROPOSING AN
# APPEND. Both numbers come from ONE measurement of the whole live corpus,
# 2026-08-12, read-only: 26 entries, 110 top-level bullets, 250 continuation
# lines.
#
# Why 3 bullets:
#   * Bullets per entry: min 1, median 4, max 11. Three shows the ENTIRE history
#     of 11 of the 26 entries and the top of the rest.
#   * The hazard is a repeat run re-stating the line above it, so what must be
#     visible is the most recent few — and same-date accumulation is already
#     REAL, not hypothetical: the worst entry in the corpus carries 6 bullets
#     sharing one date, and 12 entries carry at least 2. Three makes a run that
#     has already appended twice today see both.
#   * Cost, which is the countervailing force — this prints at the top of a
#     `/handoff` and a run can match several entries: a 3-bullet window costs a
#     median of 9 lines per entry and at most 21 under the per-bullet cap below.
#     Ten would cost the median entry its whole history and turn a multi-entry
#     run into a page nobody reads, which is how the prose guard failed already.
# Not a safety bound: showing too few is a missed catch, never a wrong write, and
# the count of bullets NOT shown is always printed.
JOURNAL_BULLETS_SHOWN = 3

# Per-bullet line cap. A bullet is WRAPPED PROSE — median 3 lines, p90 5, max 19
# in the corpus — so 6 renders more than 90% of real bullets whole, and the 7 that
# are longer are clipped with the remainder PRINTED, never silently. The point of
# showing a bullet is recognizing it, and the first six lines of a 19-line bullet
# are more than enough to recognize; the whole thing is one `--json` away.
JOURNAL_BULLET_MAX_LINES = 6


# --- Errors --------------------------------------------------------------------


class TouchError(Exception):
    """Base for every error this module raises."""


class StoreMissingError(TouchError):
    """The store root does not exist. Sentinel: 'store root not found'."""


class GitError(TouchError):
    """A git invocation failed. Sentinel: 'git command failed'.

    🔴 `stderr` IS CARRIED AS AN ATTRIBUTE, NOT ONLY INSIDE THE MESSAGE, so that a
    WRAPPING error can quote git's own words WITHOUT embedding this class's
    sentinel in its own text. `_repo_slug` is that wrapper, and it is where this
    was found: its `RepoRemoteError` interpolated the whole `GitError`, so its
    message carried two sentinels at once and "which guard fired" stopped being
    measurable — `TestPrNegativeControls._only` caught it on the first run.
    """

    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


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


# --- PR-source errors ----------------------------------------------------------
#
# 🔴 THE FIRST SOURCE THAT LEAVES THIS MACHINE. Every other source reads local
# disk, so its whole failure surface is "the file is missing" and "the file is
# malformed". This one crosses a network and an authenticated API, and each new
# way it can fail is a new way to arrive at ZERO PATHS while looking like a clean
# read:
#
#     gh is not installed · gh is not authenticated · the network is down · the
#     API errored · the rate limit is exhausted · the PR does not exist · the PR
#     is in ANOTHER repository · the response is malformed · GitHub silently
#     TRUNCATED the file list
#
# EVERY ONE RAISES, with its own sentinel and a non-zero exit, and NONE returns
# an empty path set: an empty set is indistinguishable from "this PR changed
# nothing", which is the confident zero this module exists to refuse. None falls
# back to git either, for the session source's reason — a plausible answer to a
# question the caller did not ask is worse than a refusal `/handoff` already
# knows how to handle (step 4 treats any non-zero exit as "write nothing").
#
# No two share a spelling; `TestPrNegativeControls` asserts that as the premise
# of its `_only` helper, over the session sentinels TOO — a cross-family
# collision would make both families' controls vacuous.


class RepoRemoteError(TouchError):
    """The repo under `--repo` has no parseable GitHub remote.

    Sentinel: 'repo has no usable github remote'. A PR number is meaningless
    without the repository it belongs to, and that repository is derived from
    the local remote rather than assumed — see `_repo_slug`.
    """


class GhMissingError(TouchError):
    """The `gh` binary is not on PATH. Sentinel: 'gh cli not found'.

    🔴 A LIVE CASE, NOT A DEFENSIVE ONE. `gh` is NOT in `REQUIRED_TOOLS` in
    `scripts/run-tests.sh` and NOT in `nativeBuildInputs` for the flake's
    `checks.pytests` — verified 2026-08-12 — so it is absent in the hermetic
    tier by construction, and may be absent on any host. The tool must degrade
    with this named error rather than a `FileNotFoundError` traceback, and no
    test may depend on `gh` existing (which is what `fetch` being injectable is
    for).
    """


class GhAuthError(TouchError):
    """`gh` has no usable credentials. Sentinel: 'gh is not authenticated'."""


class GhRateLimitError(TouchError):
    """The GitHub API rate limit is exhausted. Sentinel: 'github api rate limit'.

    Its own error because it is the one failure that is purely temporal: the
    same command succeeds later, so telling it apart from a real API error is
    the difference between "wait" and "something is wrong".
    """


class GhApiError(TouchError):
    """Any other `gh` failure. Sentinel: 'github api call failed'.

    🔴 THE FALLBACK, AND DELIBERATELY THE WIDEST. An unrecognised gh failure —
    an unreachable network, a 5xx, a TLS problem, a future gh message nobody
    here has seen — lands HERE rather than being mapped onto a specific
    diagnosis it might not be. Guessing wrong about WHY would be a confident
    wrong answer; the sentence carries gh's own stderr verbatim so the reader
    diagnoses it, not this module.
    """


class PrNotFoundError(TouchError):
    """No such pull request. Sentinel: 'pull request not found'.

    Also raised for a token that is not a usable PR number at all, following the
    session source's precedent for an unusable id: the caller passed something
    that cannot name a PR, and searching for it would be theatre.
    """


class PrRepoMismatchError(TouchError):
    """The PR belongs to a different repository than `--repo`.

    Sentinel: 'pull request belongs to another repository'.

    🔴 A NUMBER ALONE IS MEANINGLESS ACROSS REPOS — every repo has a #1. Reading
    another repo's file list here would MANUFACTURE associations: the paths
    would be well-formed, repo-relative and completely unrelated, and they would
    resolve against this repo's index without a murmur. The check compares the
    HOST too, not just `owner/name`: a repo whose origin is on another forge can
    share `owner/name` with an unrelated GitHub project, and an owner/name-only
    comparison would call that a match.
    """


class PrNotLandedError(TouchError):
    """The PR was closed without merging. Sentinel: 'pull request is closed unmerged'.

    🔴 OPEN AND MERGED ARE ACCEPTED; CLOSED-UNMERGED IS NOT, and the asymmetry is
    the question this source answers — "what did this branch LAND". A merged PR
    landed. An OPEN one is proposed and is the ordinary case at `/handoff` time,
    when CI is still running or review has not happened: refusing it would make
    the tool useless in exactly the moment it is invoked. A CLOSED-unmerged PR
    was proposed and REJECTED — its paths exist in no tree, so a journal bullet
    written from them would record a change to a subsystem that never happened.
    """


class PrResponseMalformedError(TouchError):
    """The API response is not the shape this code reads.

    Sentinel: 'pull request response is malformed'.

    🔴 `files` ABSENT IS NOT `files: []`. The same distinction the session source
    draws between an unobservable file set and an observed-empty one, at the
    other end of the pipe: a response missing the key means we do not know what
    the PR changed, while `[]` means GitHub says it changed nothing. Conflating
    them turns a broken read into "this PR touched nothing", which is the silent
    zero again.
    """


class PrFileListTruncatedError(TouchError):
    """GitHub returned fewer files than the PR actually changed.

    Sentinel: 'pull request file list is truncated'.

    🔴 MEASURED, NOT DEFENSIVE — and this is a LIVE hazard, not a hypothetical.
    `gh pr view --json files` caps the list at 100 entries while `changedFiles`
    reports the true count, and it says NOTHING about having done so. Measured
    2026-08-12 at three points either side of the cap:

        a  39-file PR    changedFiles=39    len(files)=39
        a 301-file PR    changedFiles=301   len(files)=100
        a 411-file PR    changedFiles=411   len(files)=100

    So on any PR over the cap the plausible-looking answer is a silent prefix,
    with a late-sorting subtree missing entirely, no error and no note. The guard
    is `len(files) < changedFiles`, which is CAP-AGNOSTIC on purpose: pinning the
    literal 100 would go stale the day GitHub changes it, and the comparison
    needs no constant at all.

    🔴 IT REFUSES rather than reporting the prefix, which is the OPPOSITE of what
    the session source does with the extractor's cap (a loud note, then carry
    on). The difference is what the caller can do about it. The extractor's cap
    is internal and its paths are still genuinely this session's; here the caller
    has an exact, provenance-preserving alternative — pipe the paths in with
    `--paths-from -`, whose caveat says outright that provenance is the caller's
    to state.
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


def scope_for_repo(repo: str | Path) -> str:
    """Ask git where `repo` really lives, then `derive_scope` it. Runs git.

    🔴 EXTRACTED SO THERE IS ONE SCOPE-DERIVATION CALL SITE, not two. `main()`
    below inlined these three lines, which was fine while this module was the
    store's only reader-of-scope. `scripts/lib/subsystem_recall.py` — the READ
    half — needs exactly the same answer, and a reader and a writer that
    disagree about which scope directory they mean is a silent, total failure:
    the writer accrues entries under one name and the reader surfaces an empty
    scope under another, which renders as "nothing recorded yet" and is
    indistinguishable from the ordinary case. So the recall module imports THIS,
    rather than re-spelling the two `rev-parse` invocations and the worktree
    rule they exist to satisfy.
    """
    repo = Path(repo)
    common = _git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"]).strip()
    top = _git(repo, ["rev-parse", "--show-toplevel"]).strip()
    return derive_scope(top, common)


# --- Path sources --------------------------------------------------------------


@dataclass(frozen=True)
class PathSource:
    """Paths plus the provenance needed to read a zero honestly.

    `commands` is the argv actually run, not a description of it: when a report
    says "0 paths", the next question is always "what did you ask?", and an
    answer that was written by hand can be wrong about the code beside it.
    """

    kind: str
    """`git`, `session`, `pr` or `caller`."""

    window: str
    """`branch` (worktree ∪ this branch's commits), `worktree`, `session`,
    `pull-requests`, or `supplied`."""

    paths: tuple[str, ...]
    base_ref: str | None = None
    commands: tuple[tuple[str, ...], ...] = ()
    """🔴 FULL ARGV, PROGRAM INCLUDED. It used to omit the program because every
    source ran `git` and the renderer hardcoded that word — which became a FALSE
    provenance line the moment a source ran something else. `commands` exists so
    a reader can check what was actually asked; a line reading `ran: git pr view
    421` would be the exact defect it is there to prevent."""

    notes: tuple[str, ...] = ()
    session: str | None = None
    """The session id, when `kind == "session"`. Part of the caveat, not decoration."""

    prs: tuple[int, ...] = ()
    """The pull requests read, when `kind == "pr"`. Part of the caveat."""

    repo_slug: str | None = None
    """`host/owner/name` the PRs were read from, when `kind == "pr"`.

    In the caveat because "PR #4" is ambiguous across repositories and the whole
    point of the repository guard is that a bare number names nothing.
    """

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
        if self.kind == "pr":
            # 🔴 THE WORDING IS THE DELIVERABLE. This source answers "what did
            # this BRANCH LAND", and nothing here may read as session
            # attribution — a PR's file list is the union of everything on the
            # branch, so it over-reports in exactly the direction the session
            # window under-reports, and it cannot say which paths are which.
            # The last sentence is imperative on purpose: the consumer is an
            # LLM about to write a dated bullet into a curated store, and
            # "session" is the word it will reach for unless told not to.
            listed = ", ".join(f"#{n}" for n in self.prs)
            return (
                f"pull request(s) {listed} in {self.repo_slug}: every file GitHub "
                f"lists on those BRANCHES — what the BRANCH LANDED, NOT what a "
                f"SESSION touched. A PR's file list is the UNION of every commit "
                f"on its branch, so it includes commits authored by ANOTHER "
                f"session, by a SUBAGENT, or by hand, and older work if the branch "
                f"is long-lived; and it EXCLUDES anything a session did that did "
                f"not reach one of these PRs. It is blind in the OPPOSITE "
                f"direction to the session window, which sees exactly one "
                f"session's turns and nobody else's. Attribute these paths to the "
                f"BRANCH, never to a session"
            )
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
            f"{proc.stderr.strip() or '(no stderr)'}",
            stderr=proc.stderr.strip(),
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
    commands.append(("git", *worktree_args))
    add(_nul_list(_git(repo, worktree_args)))

    untracked_args = ["ls-files", "--others", "--exclude-standard", "-z"]
    commands.append(("git", *untracked_args))
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
            commands.append(("git", *branch_args))
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
    #
    # 🔴 THE ORDER IS THE TAILER'S, NOT A CONVENIENCE. Inserting the collector
    # root first and the tailer's own directory second leaves the DIRECTORY
    # AHEAD of the root — which is exactly the constraint the tailer states for
    # itself ("APPENDED, not inserted at 0: the collector root also holds
    # `collector.py`, `deadman.py`, `invocation.py` and `ch_regrowth.py`, and
    # putting it ahead of this file's own directory would let any of those shadow
    # a sibling module"). Reverse this loop and a sibling can be shadowed.
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
        commands=(("git", "rev-parse", "--show-toplevel"),),
        notes=tuple(notes),
        session=label,
    )


# --- The PR source: paths from what the BRANCH LANDED --------------------------
#
# 🔴 READ `PathSource.caveat` FOR WHAT THIS SET IS BEFORE READING WHAT IT DOES.
# It answers "what did this BRANCH land", NOT "what did this session touch", and
# the two are not approximations of each other — see the module docstring.
#
# 🔴 THE FETCH IS INJECTABLE SO THE GUARDS ARE TESTABLE WITHOUT A NETWORK, AND
# WITHOUT `gh`. `gh` is NOT in `REQUIRED_TOOLS` in `scripts/run-tests.sh` and NOT
# in `nativeBuildInputs` for the flake's `checks.pytests` (verified 2026-08-12),
# so the hermetic tier has no `gh` at all and a test that shelled out would skip
# — and `run-tests.sh` pins `EXPECTED_SKIPS` exactly, so a new skip breaks the
# gate on purpose. Every guard below is therefore reached with a fixture payload
# through `fetch`, and the ONE thing that cannot be: `_gh_fetch_pr` itself, whose
# classification of gh's own exit codes is pinned separately against the
# measured stderr strings rather than by running gh.

PR_JSON_FIELDS = "number,url,state,changedFiles,files"
"""The fields fetched. `changedFiles` is not decoration: it is the ONLY way to
detect that `files` was truncated — see `PrFileListTruncatedError`."""

PR_ACCEPTED_STATES: tuple[str, ...] = ("OPEN", "MERGED")
_PR_KNOWN_STATES: tuple[str, ...] = ("OPEN", "MERGED", "CLOSED")

# The remote the repository identity is derived from. Not configurable: a second
# remote is a second answer to "which repo is this", and picking between them is
# the kind of guess this module refuses everywhere else.
_PR_REMOTE = "origin"


def _parse_remote_slug(url: str) -> str | None:
    """`host/owner/name` from a git remote URL, or None if it names no repo.

    🔴 THE HOST IS PART OF THE IDENTITY. Dropping it would let a repo whose
    origin lives on another forge match an unrelated GitHub project that happens
    to share `owner/name` — and this function's output is exactly what the
    repository guard compares, so an owner/name-only slug would make that guard
    agree with the wrong project.

    Handles the three spellings git actually emits: scp-like
    (`git@host:owner/name`), ssh URL (`ssh://git@host/owner/name`) and https
    (`https://host/owner/name`), each with or without a trailing `.git`.
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return None
    if u.endswith(".git"):
        u = u[: -len(".git")]
    if "://" not in u and ":" in u:  # scp-like
        host, _, tail = u.partition(":")
    else:
        rest = u.partition("://")[2] or u
        host, _, tail = rest.partition("/")
    host = host.rpartition("@")[2].partition(":")[0].strip().lower()
    parts = [p for p in tail.split("/") if p]
    if not host or len(parts) < 2 or not parts[-2] or not parts[-1]:
        return None
    return f"{host}/{parts[-2]}/{parts[-1]}"


def _repo_slug(repo: str | Path) -> str:
    """The `host/owner/name` a PR number is to be interpreted against.

    DERIVED from the local remote, never assumed and never taken from the
    caller: a PR number is meaningless without its repository, and the whole
    point of the repository guard is that the number alone names nothing.
    """
    try:
        url = _git(Path(repo), ["remote", "get-url", _PR_REMOTE]).strip()
    except GitError as exc:
        # 🔴 `exc.stderr`, NOT `exc`. Interpolating the whole GitError would put
        # its sentinel inside this one's message, and a message carrying two
        # sentinels makes "which guard fired" an inference instead of a
        # measurement — see `GitError`.
        raise RepoRemoteError(
            f"repo has no usable github remote: `{_PR_REMOTE}` could not be read in "
            f"{repo} ({exc.stderr or 'no detail'}) — a pull request number cannot be "
            f"resolved without knowing which repository it belongs to"
        ) from exc
    slug = _parse_remote_slug(url)
    if slug is None:
        raise RepoRemoteError(
            f"repo has no usable github remote: the `{_PR_REMOTE}` remote of {repo} is "
            f"{url!r}, which names no host/owner/name — a pull request number cannot "
            f"be resolved against it"
        )
    return slug


def _gh_argv(slug: str, number: int) -> tuple[str, ...]:
    return ("gh", "pr", "view", str(number), "--repo", slug, "--json", PR_JSON_FIELDS)


def _classify_gh_failure(rc: int, stderr: str, slug: str, number: int) -> TouchError:
    """Map a non-zero `gh` exit onto ONE named error. Never onto an empty set.

    ⚠ DECLARED AS PART-HEURISTIC, per `claude/RULES.md` ("if you reach for a
    prose/keyword patch, say so explicitly"). `gh` exposes exactly ONE
    distinguishing exit code and returns 1 for everything else, so only the auth
    case can be read structurally; the rest is keyed on stderr text. MEASURED
    2026-08-12 against the live binary (gh 2.96.0):

        no credentials at all   rc=4   "please run: gh auth login"
        bad token               rc=1   "HTTP 401: Bad credentials"
        unreachable host        rc=1   "error connecting to <host>"
        nonexistent PR          rc=1   "Could not resolve to a PullRequest"

    🔴 THE ORDER IS FORCED BY OVERLAP, NOT BY TASTE. A rate-limited response is
    an HTTP 403 that ALSO carries gh's "authenticate with gh auth login" hint, so
    an auth-first order would swallow it and report a permanent failure for a
    temporary one. Rate limit is therefore tested first, then not-found, then
    auth, and everything else falls through.

    🔴 THE FALLBACK IS THE WIDE ONE. An unrecognised failure — the unreachable
    host above, a 5xx, a gh message written after this was — becomes `GhApiError`
    carrying gh's stderr verbatim, rather than being guessed onto a specific
    diagnosis. A wrong explanation of a failure forecloses the next question.
    """
    text = (stderr or "").strip()
    low = text.lower()
    where = f"(gh pr view {number} --repo {slug}, exit {rc}): {text or '(no stderr)'}"
    if rc == 4:  # structural: gh's own "not authenticated" exit code
        return GhAuthError(f"gh is not authenticated {where}")
    if "rate limit" in low:
        return GhRateLimitError(f"github api rate limit {where}")
    if (
        "could not resolve to a pullrequest" in low
        or "no pull requests found" in low
        or "http 404" in low
    ):
        return PrNotFoundError(f"pull request not found {where}")
    if "http 401" in low or "bad credentials" in low or "gh auth login" in low:
        return GhAuthError(f"gh is not authenticated {where}")
    return GhApiError(f"github api call failed {where}")


def _gh_fetch_pr(slug: str, number: int) -> object:
    """The live fetcher. The ONLY thing here that touches the network."""
    argv = list(_gh_argv(slug, number))
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as exc:
        # 🔴 Not a traceback. `gh` is genuinely absent in the hermetic test tier
        # and may be absent on any host; `/handoff` prints this line verbatim.
        raise GhMissingError(
            f"gh cli not found: `{argv[0]}` is not on PATH, so pull request #{number} "
            f"in {slug} cannot be read. Install the GitHub CLI, or use a different "
            f"path source."
        ) from exc
    if proc.returncode != 0:
        raise _classify_gh_failure(proc.returncode, proc.stderr, slug, number)
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise PrResponseMalformedError(
            f"pull request response is malformed: gh exited 0 for #{number} in {slug} "
            f"but its stdout is not JSON ({exc})"
        ) from exc


def _pr_number(token: object) -> int:
    """A usable PR number, or a named refusal. Never a search for junk."""
    t = str(token).strip().lstrip("#")
    if not (t.isascii() and t.isdigit()) or int(t) < 1:
        raise PrNotFoundError(
            f"pull request not found: {token!r} is not a usable pull request number — "
            f"pass the integer from the PR's URL, e.g. `--pr 421` or `--pr 421,350`"
        )
    return int(t)


def _slug_from_pr_url(url: object) -> str | None:
    """`host/owner/name` out of a PR's own `url`, or None.

    This is the PR's OWN account of where it lives, which is what makes the
    repository check a check rather than a restatement of the argument we passed.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    rest = url.strip().partition("://")[2] or url.strip()
    parts = [p for p in rest.split("/") if p]
    if len(parts) < 3:
        return None
    host = parts[0].rpartition("@")[2].partition(":")[0].lower()
    if not host or not parts[1] or not parts[2]:
        return None
    return f"{host}/{parts[1]}/{parts[2]}"


def _read_pr_payload(payload: object, *, number: int, slug: str) -> tuple[list[str], str, int]:
    """(paths, state, changed_files) — or a named error. Never a short set.

    🔴 GUARD ORDER IS REACHABILITY ORDER. Each guard is reached by an input no
    EARLIER guard rejects, so each one's negative control fails on its OWN
    sentinel rather than on a neighbour's:

      1. the response is an OBJECT          PrResponseMalformedError
      2. it is the PR we ASKED for          PrResponseMalformedError
      3. its `url` names a repository       PrResponseMalformedError
      4. that repository is THIS one        PrRepoMismatchError
      5. its state is known                 PrResponseMalformedError
      6. …and it is not closed-unmerged     PrNotLandedError
      7. `files` is PRESENT and a list      PrResponseMalformedError
      8. every entry names a path           PrResponseMalformedError
      9. `changedFiles` is a count          PrResponseMalformedError
     10. …and NOTHING WAS CUT               PrFileListTruncatedError

    (4) must precede (7): the point of the repository guard is that another
    repo's file list is never READ, not merely never reported — a set read first
    and rejected later has already been in memory next to this repo's index.

    (10) is last because it is the only guard that needs the parsed list to
    exist; it is also the one that fires on a perfectly valid, perfectly
    authenticated, perfectly on-topic response. See `PrFileListTruncatedError`.
    """
    where = f"pull request #{number} in {slug}"
    if not isinstance(payload, _abc.Mapping):
        raise PrResponseMalformedError(
            f"pull request response is malformed: {where} came back as "
            f"{type(payload).__name__}, not an object"
        )

    got = payload.get("number")
    if isinstance(got, bool) or not isinstance(got, int) or got != number:
        raise PrResponseMalformedError(
            f"pull request response is malformed: asked for {where} and got number "
            f"{got!r} — the response does not describe the pull request requested"
        )

    got_slug = _slug_from_pr_url(payload.get("url"))
    if got_slug is None:
        raise PrResponseMalformedError(
            f"pull request response is malformed: {where} carries no parseable `url` "
            f"({payload.get('url')!r}), so the repository it belongs to cannot be "
            f"checked — and an unchecked repository is exactly what makes a bare "
            f"number dangerous"
        )
    if got_slug.lower() != slug.lower():
        raise PrRepoMismatchError(
            f"pull request belongs to another repository: #{number} lives in "
            f"{got_slug}, but --repo resolves to {slug}. Every repository has a #1, "
            f"so a number alone names nothing; reading that file list here would "
            f"associate another project's work with this one."
        )

    state = payload.get("state")
    if not isinstance(state, str) or state.strip().upper() not in _PR_KNOWN_STATES:
        raise PrResponseMalformedError(
            f"pull request response is malformed: {where} has state {state!r}, which is "
            f"none of {', '.join(_PR_KNOWN_STATES)} — whether it landed cannot be read"
        )
    state = state.strip().upper()
    if state not in PR_ACCEPTED_STATES:
        raise PrNotLandedError(
            f"pull request is closed unmerged: {where} was closed without merging, so "
            f"its files exist in no tree and nothing landed from it. Drop it from "
            f"--pr; {' and '.join(PR_ACCEPTED_STATES)} are accepted."
        )

    files = payload.get("files")
    if not isinstance(files, list):
        raise PrResponseMalformedError(
            f"pull request response is malformed: {where} has no `files` list "
            f"({files!r}). An ABSENT file list means the changed files are UNKNOWN, "
            f"which is not the same as a PR that changed nothing — reporting the "
            f"second for the first is the silent zero this refuses."
        )

    paths: list[str] = []
    for i, item in enumerate(files):
        raw_path = item.get("path") if isinstance(item, _abc.Mapping) else None
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise PrResponseMalformedError(
                f"pull request response is malformed: {where} file entry {i} names no "
                f"path ({item!r}) — a file list with an unreadable entry is not a "
                f"shorter file list"
            )
        paths.append(raw_path.strip())

    changed = payload.get("changedFiles")
    if isinstance(changed, bool) or not isinstance(changed, int) or changed < 0:
        raise PrResponseMalformedError(
            f"pull request response is malformed: {where} has changedFiles={changed!r}, "
            f"so whether the file list was truncated cannot be determined — and an "
            f"undetectable truncation is a silently short path set"
        )
    if len(paths) < changed:
        raise PrFileListTruncatedError(
            f"pull request file list is truncated: {where} changed {changed} files but "
            f"the API returned only {len(paths)}. The list is a PREFIX, so a "
            f"late-sorting subtree may be missing entirely and every count below it "
            f"would be wrong. Nothing is reported. Supply the paths yourself instead: "
            f"`git diff --name-only <base>...<head> | subsystem_touch.py --paths-from -`, "
            f"whose caveat states that provenance is the caller's."
        )
    return paths, state, changed


def collect_pr_paths(
    repo: str | Path,
    numbers: Iterable[object],
    *,
    fetch=None,
    exclude: Iterable[str] = (),
) -> PathSource:
    """What the named PULL REQUESTS landed. Per-branch, not per-session.

    The window the other two sources cannot express. `--session` is blind to a
    SUBAGENT's edits — a separate transcript, 196 of 733 file-tool calls measured
    — and delegating non-trivial work to a subagent is the standing default here,
    so on the sessions worth recording the implementation is exactly what the
    session window misses. Git is blind to anything already merged. A PR's file
    list is blind to neither.

    🔴 AND IT IS BLIND IN THE OPPOSITE DIRECTION: it is the union of every commit
    on the branch, including another session's and hand-made ones, and it omits
    whatever a session did that never reached a PR. `PathSource.caveat` says so
    in those words, on every output path of both renderers. Do not describe this
    set as a session's.

    `fetch` is the injection seam: a callable `(slug, number) -> payload`,
    defaulting to `_gh_fetch_pr`. Every guard in `_read_pr_payload` is reachable
    through it with a fixture, which is what keeps the suite off the network and
    off `gh` — neither of which exists in the hermetic tier.

    🔴 `commands` RECORDS THE ARGV ONLY WHEN THE ARGV RAN. With a fetcher
    injected nothing was executed, so recording `gh pr view …` would be a
    fabricated provenance line in the field whose entire purpose is to say what
    was actually asked. An injected run says so in a note instead.
    """
    slug = _repo_slug(repo)
    live = fetch is None
    fetcher = _gh_fetch_pr if live else fetch

    commands: list[tuple[str, ...]] = []
    notes: list[str] = []
    raw: list[str] = []
    read: list[int] = []

    for token in numbers:
        n = _pr_number(token)
        if n in read:
            notes.append(f"pull request #{n} was named more than once; read once")
            continue
        read.append(n)
        if live:
            commands.append(_gh_argv(slug, n))
        paths, state, changed = _read_pr_payload(fetcher(slug, n), number=n, slug=slug)
        # 🔴 EMITTED UNCONDITIONALLY, INCLUDING AT ZERO. A stated `0 file(s)` is
        # a reading; an absent line is indistinguishable from a fetcher wired to
        # nothing (`claude/RULES.md` → "report the pair").
        notes.append(
            f"pull request #{n} ({state}): {changed} file(s) reported by the API, "
            f"{len(paths)} read"
        )
        raw.extend(paths)

    if not read:
        # Reachable via `--pr ,` or `--pr ''`. Falling through would return a
        # perfectly well-formed report over ZERO pull requests — the confident
        # zero, arriving through argument parsing.
        raise PrNotFoundError(
            "pull request not found: no pull request number was given, so nothing was "
            "read. `--pr` takes one or more integers, e.g. `--pr 421,350`."
        )
    if not live:
        notes.insert(
            0,
            "⚠ paths came from an INJECTED fetcher, not from `gh` — this is a test "
            "harness, not a live read",
        )

    paths, dropped = _filter_excluded(raw, exclude)
    notes.append(
        f"{len(paths)} distinct path(s) across {len(read)} pull request(s) in {slug}"
    )
    if dropped:
        notes.append(_exclusion_note(dropped))

    return PathSource(
        kind="pr",
        window="pull-requests",
        paths=tuple(paths),
        commands=tuple(commands),
        notes=tuple(notes),
        prs=tuple(read),
        repo_slug=slug,
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


# --- What the entry ALREADY says -----------------------------------------------
#
# 🔴 WHY THIS EXISTS, MEASURED. The `KNOWN ENTRIES` block used to print the
# append SHAPE and the insertion point and nothing else — so the agent deciding
# whether to append could not see the bullet it was about to duplicate. Verified
# 2026-08-12 by re-running the writer against an entry appended to ~20 minutes
# earlier: the block was byte-identical to the first run. The only guard was
# prose in `handoff/SKILL.md` ("Nothing notable ⇒ propose nothing"), which asks
# an agent to judge notability immediately after work it feels good about, with
# the prior bullet invisible.
#
# It is not hypothetical that this accumulates: in the live corpus one entry
# already carries 6 bullets sharing a single date, and 12 of 26 carry at least 2.
#
# 🔴 READ-AND-DISPLAY ONLY. This module has NO write call site and that property
# is what lets it be pointed at a curated, client-confidential, unbacked-up
# store; `TestNeverWrites` hashes a whole store tree either side of every mode.
# Everything below reads.


@dataclass(frozen=True)
class EntryJournal:
    """What `## Nuance / work-history` ALREADY holds for one matched entry.

    🔴 THE EMPTY CASES ARE NAMED, NOT COLLAPSED. Four different things produce
    "no bullets to show", and they mean different things to an agent about to
    write one — see `state`. Rendering all four as a blank space is precisely the
    confident zero this toolchain keeps paying for: an empty display would be
    indistinguishable from a parser wired to nothing.
    """

    ref: str
    filename: str
    state: str
    """`journalled` | `section-absent` | `section-empty` | `unbulleted`.

    `section-absent`   the entry has no `## Nuance / work-history` heading at
                       all. Ordinary for an `/analyze-service`-written entry —
                       and worth saying LOUDLY, because the skill's append
                       anchors an `Edit` on that heading, so the append cannot
                       land until the heading is created.
    `section-empty`    the heading is there with nothing under it. The writer's
                       own `new_entry_template` never produces this (it ships a
                       first bullet), so it means someone emptied it.
    `unbulleted`       the section has prose but no top-level `- ` bullet. Not a
                       parse failure to hide: it is a schema violation the agent
                       should see before appending a bullet beneath it.
    `journalled`       at least one bullet was parsed.
    """

    bullets: tuple[JournalBullet, ...] = ()
    """ALL of them, in stored order. The display cap is applied by the renderer,
    so `--json` carries the whole history and the count of what was hidden is
    always derivable."""

    created_by: str | None = None
    """The entry's front-matter `created_by`, or None for an entry predating the
    stamp. 🔴 IT ATTRIBUTES THE ENTRY, NOT THE NEWEST BULLET. There is no
    per-bullet writer field in the schema, so who wrote the most recent line is
    NOT recorded and this must never be presented as if it were."""

    @property
    def dated(self) -> tuple[str, ...]:
        return tuple(b.date for b in self.bullets if b.date)

    @property
    def newest_date(self) -> str | None:
        """The recency signal: the MAXIMUM date across the bullets, or None.

        🔴 DERIVED FROM CONTENT, NOT FROM `mtime`, and the two are not
        interchangeable. The store is a git working tree under an hourly
        autocommit that other sessions also write to, so a file's mtime moves
        for a checkout, a `git add`, a touch of the front matter, or an edit to
        a completely different section — every one of which would report the
        journal as "just appended to" when nothing was appended. A bullet's date
        is what the last appender actually claimed.

        🔴 THE MAXIMUM, not the first bullet's date. Newest-first is the store's
        CONVENTION, and a convention is not an invariant — if a past appender put
        its line at the bottom, reading position as recency reports the oldest
        bullet as the newest. `max()` is right whichever way they are ordered.
        Undated bullets (44% of the corpus) are skipped, not guessed at.
        """
        return max(self.dated) if self.dated else None

    def dated_on(self, day: str) -> int:
        """How many bullets already carry `day`. The repeat-run signal."""
        return sum(1 for d in self.dated if d == day)

    def days_since(self, today: str) -> int | None:
        """Days from `newest_date` to `today`, or None if neither is a date.

        No clock: `today` is injected all the way from the CLI, so this stays a
        pure function of the report. The import is LOCAL for the same reason the
        one in `main()` is — `date` at module scope would put `date.today()`
        within reach of this layer, which is exactly what `today: str` exists to
        keep out. `fromisoformat` on two injected strings is arithmetic, not a
        clock, and a garbage `today` returns None rather than a wrong number.
        """
        newest = self.newest_date
        if newest is None:
            return None
        from datetime import date

        try:
            return (date.fromisoformat(today) - date.fromisoformat(newest)).days
        except ValueError:
            return None


def read_entry_journal(store_root: str | Path, entry: SubsystemEntry) -> EntryJournal:
    """Read ONE entry's existing journal. READ-ONLY.

    The file is located from the loader's own `scope` + `filename`, never from a
    path rebuilt out of the ref — `<slug>.<kind>.md` and `<slug>.md` are
    different files and only the loader knows which one this entry came from.

    ⚠ REACHABILITY OF THE `EntryUnreadableError` HERE, stated rather than
    implied. In `build_report`'s flow it is normally the SECOND read of this
    file: `load_index` has already read every `*.md` in the store, so a
    permanently unreadable entry raises from the wrap around THAT call and this
    one never executes. This wrap covers the case that one cannot — the file
    becoming unreadable BETWEEN the two reads, which is a live possibility in a
    store with an hourly autocommit and concurrent sessions — and it is reached
    directly by callers of this function, which is a public entry point. The
    mutation test drives it by direct call for that reason.
    """
    path = Path(store_root) / entry.scope / entry.filename
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise EntryUnreadableError(
            f"index entry unreadable: {path} ({type(exc).__name__}: {exc}) — the entry's "
            f"existing work-history could NOT be read, so nothing can be said about what "
            f"it already records; propose no append. Nothing was written"
        ) from exc

    created_by = parse_front_matter(text).get("created_by")
    body = extract_sections(text, (NUANCE_HEADING,)).get(NUANCE_HEADING)
    common = {
        "ref": entry.ref,
        "filename": entry.filename,
        "created_by": created_by if isinstance(created_by, str) and created_by else None,
    }
    if body is None:
        return EntryJournal(state="section-absent", **common)
    if not body.strip():
        return EntryJournal(state="section-empty", **common)
    bullets = parse_journal_bullets(body)
    if not bullets:
        return EntryJournal(state="unbulleted", **common)
    return EntryJournal(state="journalled", bullets=bullets, **common)


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

    journals: Mapping[str, EntryJournal] = field(default_factory=dict)
    """ref -> what that entry's `## Nuance / work-history` ALREADY holds.

    Populated for MATCHED entries only — the ones a bullet would be proposed
    against. Below-threshold and ambiguous entries get none: no append is
    proposed for them, so there is nothing to compare against, and reading them
    would put more of a client-confidential store on screen than the decision
    needs.
    """

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

    # 🔴 The loader's OSErrors are NAMED. `load_index` reads every `*.md` under
    # the store, and an unreadable one — a directory sitting where a `.md` is
    # expected, a bad mode, a mid-rename file in a tree an hourly autocommit and
    # other sessions are also touching — would otherwise reach `main()` as a bare
    # `IsADirectoryError` with nothing saying the SUBSYSTEM STORE was what failed.
    # Same class `subsystem_recall` raises for the same condition, imported from
    # the resolver rather than re-declared.
    #
    # ⚠ THE `MalformedEntryError` CLAUSE IS REDUNDANT-BUT-KEPT, and it is
    # labelled because a mutation test PROVED it unkillable rather than because
    # anyone reasoned about it: `MalformedEntryError` is a `ResolverError`, not
    # an `OSError`, so the clause below could never have caught it and the
    # re-raise changes nothing. Replacing it with `except ZeroDivisionError`
    # leaves the behaviour identical. It stays — spelled the same way in
    # `subsystem_recall`, which reads the same store — because it makes
    # "the store is BROKEN is not reworded as the store is UNREADABLE" readable
    # at the call site, and because deleting it from one of the two readers and
    # not the other is how they start to drift. It is NOT coverage: nothing in
    # the suite can measure it, and `test_the_MALFORMED_reraise_is_UNKILLABLE`
    # says so out loud instead of leaving a green that means nothing.
    try:
        index = load_index(store)
    except MalformedEntryError:
        raise
    except OSError as exc:
        raise EntryUnreadableError(
            f"index entry unreadable: under {store} ({type(exc).__name__}: {exc}) — the "
            f"store was not fully read, so this association would be INCOMPLETE and an "
            f"entry could look untouched purely because its file could not be opened"
        ) from exc

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
        journals={m.entry.ref: read_entry_journal(store, m.entry) for m in assoc.matched},
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


def _render_recency(j: EntryJournal, today: str) -> str:
    """The one-line recency signal for an entry, WHICHEVER of the five it is."""
    stamp = (
        f"entry created_by={j.created_by}"
        if j.created_by
        else "entry created_by not recorded (predates the stamp)"
    )
    if j.state == "section-absent":
        return (
            f"journal: NO `{NUANCE_HEADING}` SECTION — nothing has ever been journalled "
            f"here, and the skill's `Edit` anchor does not exist yet, so the heading has "
            f"to be created as part of the append; {stamp}"
        )
    if j.state == "section-empty":
        return (
            f"journal: `{NUANCE_HEADING}` is present and EMPTY — the heading is there with "
            f"nothing under it, so nothing can be duplicated; {stamp}"
        )
    if j.state == "unbulleted":
        return (
            f"journal: `{NUANCE_HEADING}` has content but NO top-level `- ` bullet — it "
            f"does not match the schema, so this display can show you nothing to compare "
            f"against. Read the section in the file before appending; {stamp}"
        )

    n = len(j.bullets)
    dated = len(j.dated)
    if j.newest_date is None:
        return (
            f"journal: {n} bullet{'' if n == 1 else 's'}, NONE dated — recency is UNKNOWN "
            f"from content. The file's mtime is deliberately not used: it moves for a "
            f"checkout, an hourly autocommit or an edit to another section, so it would "
            f"report an append that never happened; {stamp}"
        )
    days = j.days_since(today)
    if days is None:
        when = "age not computable"
    elif days < 0:
        when = f"dated {-days} day{'' if -days == 1 else 's'} in the FUTURE relative to {today}"
    elif days == 0:
        when = "TODAY"
    else:
        when = f"{days} day{'' if days == 1 else 's'} ago"
    return (
        f"journal: {n} bullet{'' if n == 1 else 's'}, newest dated {j.newest_date} ({when}), "
        f"{dated} of {n} dated; {stamp}"
    )


def _render_journal(j: EntryJournal, today: str, indent: str) -> list[str]:
    """The per-entry `already there` block: recency, the repeat warning, bullets.

    🔴 EXISTING BULLETS ARE PREFIXED `|` AND THE PROPOSAL IS NOT. The one thing
    that must never blur on screen is which lines the entry ALREADY has and which
    line the agent is about to invent, and an unprefixed quote of curated prose
    sitting under an unprefixed append shape blurs exactly that.
    """
    out = [f"{indent}{_render_recency(j, today)}"]
    repeats = j.dated_on(today)
    if repeats:
        # 🔴 THE LOUD ONE. This is the measured failure: a second or third
        # `/handoff` in one day proposing another line dated the same day, with
        # the ones already there invisible.
        out.append(
            f"{indent}🔴 {repeats} bullet{'' if repeats == 1 else 's'} on this entry "
            f"{'is' if repeats == 1 else 'are'} ALREADY dated {today}. A further "
            f"same-dated bullet is the accumulation this block exists to prevent — append "
            f"only if you can say what it adds that the line{'' if repeats == 1 else 's'} "
            f"below do{'es' if repeats == 1 else ''} not."
        )
    if not j.bullets:
        return out
    shown = j.bullets[:JOURNAL_BULLETS_SHOWN]
    total = len(j.bullets)
    # ⚠ "top N, in stored order" — NOT "the newest N". Newest-first is the
    # store's convention, and `newest_date` above is computed with `max()`
    # precisely because a convention is not an invariant. A header that called
    # these the newest would be making a claim this module refuses to make one
    # line earlier.
    out.append(
        f"{indent}already there — READ THESE BEFORE PROPOSING "
        + (
            f"(all {total}):"
            if total <= JOURNAL_BULLETS_SHOWN
            else f"(top {len(shown)} of {total} in stored order; convention is newest-first):"
        )
    )
    for b in shown:
        lines = list(b.lines[:JOURNAL_BULLET_MAX_LINES])
        for line in lines:
            out.append(f"{indent}  | {line}")
        clipped = len(b.lines) - len(lines)
        if clipped:
            # Printed, never silent — a bullet cut off mid-sentence is one an
            # agent can fail to recognize as its own line from an hour ago.
            out.append(f"{indent}  | … +{clipped} more line{'' if clipped == 1 else 's'} of this bullet")
    if total > len(shown):
        rest = total - len(shown)
        out.append(
            f"{indent}  … {rest} further bullet{'' if rest == 1 else 's'} not shown "
            f"(display cap, not a judgement — `--json` carries all of them)"
        )
    return out


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
        # The FULL argv, program included. See `PathSource.commands`: the word
        # `git` used to be hardcoded here, which made this line a false claim
        # the moment a source ran something else.
        out.append(f"  ran: {' '.join(cmd)}")

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
            journal = report.journals.get(m.entry.ref)
            if journal is not None:
                out.extend(_render_journal(journal, report.today, "      "))
            else:
                # 🔴 Said, never left blank. A matched entry with no journal read
                # is a bug in this module, and printing nothing for it renders
                # identically to an entry with an empty history — the exact
                # confounding this block exists to remove.
                out.append(
                    "      journal: NOT READ — this entry matched but its existing "
                    "work-history was never loaded. Do not propose an append; you cannot "
                    "see what it already says."
                )
        out.append(f"  append shape: {journal_line_shape(report.today)}")
        out.append(
            "  🔴 COMPARE what you write against the `already there` lines above. Restating "
            "a bullet the entry already carries — in other words, re-recording work whose "
            "lesson is on screen — is the failure this display exists to prevent; nothing "
            "notable that is not already there ⇒ propose nothing and say `index unchanged`."
        )
        out.append(
            "  (`created_by` attributes the ENTRY. The schema records no per-bullet writer, "
            "so who wrote the newest bullet is not recorded anywhere.)"
        )
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


def _journal_json(j: EntryJournal | None, today: str) -> dict | None:
    if j is None:
        return None
    return {
        "state": j.state,
        "created_by": j.created_by,
        "bullet_count": len(j.bullets),
        "dated_count": len(j.dated),
        "newest_date": j.newest_date,
        "recency_source": "newest bullet date (NOT file mtime)",
        "days_since_newest": j.days_since(today),
        "dated_today": j.dated_on(today),
        "bullets": [{"date": b.date, "text": b.text, "lines": len(b.lines)} for b in j.bullets],
    }


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
            "prs": list(src.prs),
            "repo_slug": src.repo_slug,
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
                # The WHOLE journal, uncapped — the text renderer's caps are a
                # display bound, and a consumer that wants to diff a proposed
                # line against the full history must not have to re-read the
                # store to get it. `None` only if the journal was not read.
                "journal": _journal_json(report.journals.get(m.entry.ref), report.today),
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
            "fallback source when no --session/--transcript/--pr is given: `git` "
            "(default) or `-` to read repo-relative paths from stdin, one per line"
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
    session_src.add_argument(
        "--pr",
        action="append",
        default=None,
        metavar="N[,N...]",
        help=(
            "report what these PULL REQUESTS landed, via `gh`. A DIFFERENT QUESTION "
            "from --session: a PR's file list is the union of every commit on its "
            "branch, so it SEES a subagent's work (which --session cannot) and also "
            "another session's (which --session correctly excludes). Repeatable and "
            "comma-separated. Validated against the repo under --repo; OPEN and "
            "MERGED are accepted, closed-unmerged is refused."
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
        scope = args.scope if args.scope is not None else scope_for_repo(repo)

        if args.template is not None:
            print(new_entry_template(normalize_ref(args.template), scope, today=stamp))
            return 0

        wants_session = args.session is not None or args.transcript is not None
        wants_pr = args.pr is not None
        # 🔴 A CONTRADICTION IS REFUSED, NOT RESOLVED. `--paths-from` names the
        # FALLBACK source, so pairing it with a session or PR request asks for
        # two different windows at once. Honouring either silently would hand
        # back a plausible answer to a question the caller did not ask — the
        # same failure the no-fallback rule above exists to prevent, arriving
        # through argument parsing instead. (`--session` vs `--transcript` vs
        # `--pr` is enforced one level up, by argparse's exclusive group.)
        if (wants_session or wants_pr) and args.paths_from != "git":
            print(
                "subsystem-touch: --session/--transcript/--pr cannot be combined with "
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
        elif wants_pr:
            # Flattened here rather than in `collect_pr_paths` so the library
            # keeps taking a plain sequence of numbers: `--pr 1,2 --pr 3` is a
            # CLI spelling, not part of the contract.
            source = collect_pr_paths(
                repo,
                # Empty tokens are dropped so `--pr 421,` is the typo it looks
                # like rather than a second, unusable number — and so `--pr ,`
                # reaches the "no pull request number was given" guard instead
                # of the "not a usable number" one, which is a different fact.
                [
                    tok
                    for group in args.pr
                    for tok in str(group).split(",")
                    if tok.strip()
                ],
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
