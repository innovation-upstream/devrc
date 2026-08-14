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
Four real sources: the SESSION's own transcript, the PULL REQUESTS a branch
landed, the COMMITS an agent created, and GIT. The reporting core takes paths as
an argument and has never heard of any of them, which is what let the other three
be added without touching the matching logic.

🔴 THEY ANSWER FOUR DIFFERENT QUESTIONS, AND THE DIFFERENCE IS NOT A NUANCE.
`--session` answers "what did THIS SESSION touch". `--pr` answers "what did THIS
BRANCH LAND". `--commit` answers "what did THESE COMMITS CHANGE". Git's window is
a fourth thing again (this branch's uncommitted + committed work, whoever
authored it). They are not four approximations of one quantity — a PR's file list
is the UNION of every commit on its branch, so it contains another session's
commits, a subagent's, and hand-made ones; a session transcript contains exactly
one session's turns and nobody else's; and a named commit contains exactly what
that commit changed, with a SIBLING commit on the same branch outside the window.
Each `caveat` says which question its own window answers; see
`PathSource.caveat`.

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
  * COMMITS (`--commit <sha>[,<sha>...]`) — the WORKFLOW-AGNOSTIC one, and the
    only source that reaches a repo which lands work without pull requests or
    forbids committing in the primary clone. Both blind spots were MEASURED on
    the repo holding 23 of the store's 24 entries: its `CLAUDE.md` mandates a
    throwaway `/tmp/wt-*` worktree, so a real `--session` run reported 25 paths
    OUTSIDE the session cwd and 0 inside; and of its last 200 mainline commits, 144
    carry no `(#N)` suffix, so 72% of what lands never passes through a PR. A
    commit is the primitive the other two reduce to — a PR is a set of commits,
    worktree-authored work becomes a mainline commit, a direct push IS a commit — so
    this source is workflow-agnostic by construction rather than by luck. What it
    costs: the shas must come from the CALLER, because nothing on the machine
    knows which commits an agent just created. See `collect_commit_paths` for the
    five guards, and for the merge/empty/unreachable decisions.
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
`--session`, `--transcript`, `--pr`, `--commit` and `--paths-from` are mutually
exclusive. Unioning `--session` with `--pr` was considered and REJECTED, and the
reason is the caveat rather than the paths — it applies verbatim to `--commit`,
one flag further along, since a three-way union would need three caveat sentences
in one line:

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
    collect_commit_paths(repo, shas)          -> PathSource      (runs git)
    collect_git_paths(repo)                   -> PathSource      (runs git)
    caller_supplied(paths)                    -> PathSource      (pure)
    nominate(assoc, index, *, min_paths, limit)
                                              -> tuple[Nomination, ...]
    build_report(source, store_root, scope, *, today, ...)
                                              -> TouchReport
    render_text(report) / report_json(report) -> str / dict
    census(store_root, now=None)              -> Census  (counts + write activity)
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

and, for the commit source — the source with the most ways to reach a SILENT
zero, because four of them are git's own exit-0 defaults (a merge, a root commit
without `--root`, and a blob or tree sha, each printing an empty file list at
exit 0). Each is FATAL and none returns an empty path set:

    "commit sha is malformed"                  CommitRefMalformedError
    "commit not found"                         CommitMissingError  (also the
                                               "created in ANOTHER repo" case,
                                               which is not locally separable)
    "commit sha is ambiguous"                  CommitAmbiguousError
    "object is not a commit"                   CommitWrongTypeError
    "commit is a merge"                        CommitIsMergeError

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
    ON_MALFORMED_COLLECT,
    AmbiguousRefError,
    Association,
    EntryUnreadableError,
    JournalBullet,
    MalformedEntry,
    MalformedEntryError,
    ResolverError,
    SubsystemEntry,
    SubsystemIndex,
    UnknownScopeError,
    associate_paths,
    entry_mapping,
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
    "EntryFileMissingError",
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
    "CommitRefMalformedError",
    "CommitMissingError",
    "CommitAmbiguousError",
    "CommitWrongTypeError",
    "CommitIsMergeError",
    "PR_JSON_FIELDS",
    "PR_ACCEPTED_STATES",
    "COMMIT_SHA_MIN_CHARS",
    "COMMIT_SHA_MAX_CHARS",
    "PathSource",
    "Nomination",
    "EntryJournal",
    "read_entry_journal",
    "TouchReport",
    "Census",
    "ValidationReport",
    "POLICY_SCOPE",
    "POLICY_STORE_ROOT",
    "POLICY_NONE",
    "governing_policy",
    "validate_entry_file",
    "validate_scope",
    "render_validation",
    "derive_scope",
    "scope_for_repo",
    "collect_git_paths",
    "collect_session_paths",
    "collect_pr_paths",
    "collect_commit_paths",
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

# --- Which policy file governs a scope ------------------------------------------
#
# 🔴 THE INSTRUCTION WAS UNFOLLOWABLE IN 80% OF CASES. The store-root README says
# "read the README inside a scope directory before touching it", and
# `handoff/SKILL.md` step 4 calls the scope README authoritative — but measured
# 2026-08-13, only 1 of the store's 5 scopes has one. An agent told to read a file
# that is not there either invents a reason to proceed or burns a round trip
# asking.
#
# The fix is deterministic and adds NO policy: the tool states WHICH file actually
# governs. It does not generate a README into a scope, and must not — each scope's
# README is a human policy statement, and writing one would be manufacturing
# authority the store never granted. Naming the store-root README as the fallback
# is the opposite: it points at policy a human already wrote.
POLICY_SCOPE = "scope README — authoritative for this scope"
POLICY_STORE_ROOT = "store-root README — this scope has none of its own"
POLICY_NONE = "NONE — neither a scope README nor a store-root README exists"


def governing_policy(store_root: str | Path, scope: str) -> tuple[str | None, str]:
    """`(path|None, basis)` — the policy file that governs writes to `<scope>/`.

    Precedence, and it is the same order the prose asserts: the scope's own
    README, else the store-root README, else neither. READ-ONLY: it stats two
    paths and reads nothing.

    Returns the BASIS as well as the path because a path alone cannot say whether
    the reader is looking at policy written for this scope or policy written for
    the store — and an agent that mistakes the second for the first will believe
    a scope has spoken when it has not.
    """
    store = Path(store_root)
    scoped = store / normalize_ref(scope) / "README.md"
    if scoped.is_file():
        return str(scoped), POLICY_SCOPE
    root = store / "README.md"
    if root.is_file():
        return str(root), POLICY_STORE_ROOT
    return None, POLICY_NONE


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


class EntryFileMissingError(TouchError):
    """`--validate <path>` names something that is not a file. Sentinel: 'entry file not found'.

    🔴 ITS OWN SENTINEL, NOT `malformed index entry`. A path that does not exist
    and a file that does not parse are different facts with different fixes
    (check the path vs. fix the front matter), and reporting the first as the
    second would make the validator's own output the thing that misleads. They do
    share an exit code, because the skill's handling of both is identical.
    """


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
    """A cross-repo session left NOTHING this repo can claim without inference.

    Sentinel: 'transcript cwd does not match'. Not a formality: the extractor's
    default window relativizes every path against the SESSION's cwd, so a
    RELATIVE path from a session rooted elsewhere is repo-relative to the WRONG
    repo — it would resolve, quietly, against this repo's index.

    🔴 THIS IS NARROWER THAN ITS NAME, AND THE OLD WORDING WAS FALSE. A cwd
    mismatch alone no longer raises. `collect_session_paths` first tries the
    ABSOLUTE window — the entries the session named as absolute paths that
    resolve under `--repo`, which need no re-anchoring and are attributable
    whatever cwd the session ran in — and raises only when that window is empty.
    The superseded message said "every path in a transcript is relative to the
    session's own cwd"; a transcript entry is the tool call's own `file_path`,
    and a measurable share of them are absolute paths into another real tree.
    Those were the ones being thrown away — see `collect_session_paths` for the
    measurement, and for why the 112 that motivated this is RETRACTED.

    What has NOT been relaxed, and must not be: a relative path is never
    re-anchored. The frame check on relative entries is the safety property; the
    over-refusal was the frame check being applied to entries that carry their
    own frame.

    🔴 THE MESSAGE NAMES THE ALTERNATIVE, because the obvious fallback is wrong
    here. For every OTHER session failure the answer is "drop --session and use
    the git window"; for this one that is a SECOND dead source — a session that
    ran in another repo left nothing in this repo's branch window either. The
    work reached here as pull requests or commits, so --pr/--commit is the only
    window that can see it. Observed live: a session whose cwd was one repo
    while all of its work landed in another.
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


# --- Commit-source errors ------------------------------------------------------
#
# 🔴 THE SOURCE WITH THE MOST WAYS TO PRODUCE A SILENT ZERO, AND THEY ARE GIT'S
# OWN DEFAULTS RATHER THAN ANYTHING THIS MODULE DOES. Measured against git 2.55.0
# on a synthetic repo, 2026-08-12 — every one of these EXITS 0 and prints an EMPTY
# path list, so a naive implementation reports "this commit changed nothing":
#
#     `diff-tree <merge>`        prints NOTHING (a merge has no single diff)
#     `diff-tree <root-commit>`  prints NOTHING without `--root`
#     `diff-tree <blob-sha>`     prints NOTHING; the complaint goes to stderr
#                                and the EXIT CODE IS 0
#     `diff-tree <tree-sha>`     same
#
# So `GitError` — which keys on a non-zero exit — cannot see any of them. Each is
# closed by a guard BEFORE the diff runs, and each guard has its own sentinel so a
# caller (or a mutation test) can tell which fired. None falls back to another
# source, for the reason the session and PR families already state: a plausible
# answer to a question the caller did not ask is worse than a refusal `/handoff`
# step 4 already knows how to handle.
#
# No two share a spelling, across BOTH other families too —
# `TestCommitNegativeControls` asserts that over the union, because a cross-family
# collision would make every family's `_only` helper vacuous.


class CommitRefMalformedError(TouchError):
    """The token is not an object name at all. Sentinel: 'commit sha is malformed'.

    🔴 ONLY A HEX SHA IS ACCEPTED — never a revision EXPRESSION (`HEAD`, `main`,
    `HEAD~3`, `@{u}`). An expression resolves against ambient repo state, so the
    same argument names a different commit tomorrow, in another worktree, or on
    another host; a window that moves under a re-run is not the deterministic,
    re-runnable source this flag exists to be. `HEAD` in particular would resolve
    silently and report a real diff — a confident answer to a token the caller
    meant as shorthand.

    The length bound is git's own: a prefix shorter than 4 is not a sha git will
    resolve for a human, and `rev-parse --disambiguate` will still EXPAND a 3-char
    prefix in a small repo (measured) — so without this bound a typo resolves.
    """


class CommitMissingError(TouchError):
    """No object in THIS repo has that name. Sentinel: 'commit not found'.

    🔴 IT DELIBERATELY COVERS "THE SHA BELONGS TO ANOTHER REPOSITORY" TOO, and
    says so in its own message rather than pretending to a diagnosis it cannot
    make. `claude/RULES.md` → "an EMPTY RESULT cannot distinguish two mechanisms":
    a sha that was never created and a sha created in a DIFFERENT clone are the
    same observable here — the object is absent — and no local signal separates
    them. (Worktrees of one repo share an object database, so a commit made in a
    throwaway worktree IS present; that is the case this flag was built for, and
    it is not this error.) A `CommitForeignRepoError` would be a status with no
    code path behind it, the same shape this module refuses for `no-store`.

    Also raised when no sha was given at all (`--commit ,`), following the PR
    source's precedent: a well-formed report over ZERO commits is the confident
    zero arriving through argument parsing.
    """


class CommitAmbiguousError(TouchError):
    """A short sha names more than one object. Sentinel: 'commit sha is ambiguous'.

    🔴 REFUSED BY NAME, NEVER SILENTLY RESOLVED, and the check is CONSERVATIVE on
    purpose: it counts candidates of EVERY object type (`rev-parse
    --disambiguate`), not only those that peel to a commit. Git will happily pick
    for you when the prefix is ambiguous between a blob and a commit — the
    type-peeling rules decide, and they are a git-version-dependent tiebreak this
    module would then be silently inheriting. The caller always has the exact
    alternative (the full 40-char sha, which it already has in hand), so refusing
    costs it nothing and removes the whole class.
    """


class CommitWrongTypeError(TouchError):
    """The sha names a blob/tree/tag, not a commit. Sentinel: 'object is not a commit'.

    🔴 A LIVE SILENT ZERO, MEASURED, NOT A DEFENSIVE CHECK. `git diff-tree
    --name-only -r --root <blob-sha>` exits **0** with empty stdout and puts
    `error: object … is a blob, not a commit` on stderr (git 2.55.0). So without
    this guard the run succeeds and reports that the "commit" changed no files —
    indistinguishable from an empty commit, and `GitError` never sees it because
    the exit code is 0.
    """


class CommitIsMergeError(TouchError):
    """The sha names a merge commit. Sentinel: 'commit is a merge'.

    🔴 REFUSED, AND THIS IS A DECISION WITH THREE REJECTED ALTERNATIVES. A merge
    has no single diff, and `git diff-tree <merge>` prints NOTHING at exit 0
    (measured) — so "do nothing special" is the silent zero.

      * FIRST-PARENT diff (`<merge>^1..<merge>`) — what the merged branch brought
        in. REJECTED: that is the whole other branch's work, attributed to one
        sha the caller named as "a commit I made". It over-reports in exactly the
        direction this source exists to avoid, and the caveat printed beside it
        would say "what these COMMITS changed", which would be false.
      * COMBINED diff (`--cc`) — only the files that differ from ALL parents, i.e.
        conflict resolutions. REJECTED: a third question again, and for a clean
        merge it is empty, so it reintroduces the silent zero for the common case.
      * EMPTY, stated in a note. REJECTED because it does not compose: `--commit
        a,b,<merge>` would quietly under-report while still printing a confident
        union.

    So it refuses and names the alternatives: the merge's own side commits, or
    `--pr <n>`, which is the source built for "what a BRANCH landed" and says so
    in its own caveat.
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
    return derive_scope(_toplevel(repo), common)


# --- Path sources --------------------------------------------------------------


@dataclass(frozen=True)
class PathSource:
    """Paths plus the provenance needed to read a zero honestly.

    `commands` is the argv actually run, not a description of it: when a report
    says "0 paths", the next question is always "what did you ask?", and an
    answer that was written by hand can be wrong about the code beside it.
    """

    kind: str
    """`git`, `session`, `pr`, `commit` or `caller`."""

    window: str
    """`branch` (worktree ∪ this branch's commits), `worktree`, `session`,
    `session-absolute`, `pull-requests`, `commits`, or `supplied`.

    🔴 `session` and `session-absolute` are DIFFERENT WINDOWS on one transcript,
    not a flag on one window. `session` is every path the session's turns named,
    read against the session's own cwd — available only when that cwd IS this
    repo. `session-absolute` is the cross-repo case: the session ran somewhere
    else, so only the paths it named ABSOLUTELY and that resolve under this repo
    are reportable, and every relative path it named is excluded because nothing
    says which tree it belonged to. They carry different caveats for that reason.
    """

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

    session_cwd: str | None = None
    """The cwd the transcript recorded, when `window == "session-absolute"`.

    In the caveat because it is the reason that window is narrow: a reader who
    cannot see which repo the session actually ran in has no way to judge how
    much of its work the absolute subset represents.
    """

    prs: tuple[int, ...] = ()
    """The pull requests read, when `kind == "pr"`. Part of the caveat."""

    repo_slug: str | None = None
    """`host/owner/name` the PRs were read from, when `kind == "pr"`.

    In the caveat because "PR #4" is ambiguous across repositories and the whole
    point of the repository guard is that a bare number names nothing.
    """

    commits: tuple[str, ...] = ()
    """The FULL 40-char shas read, when `kind == "commit"`.

    Full, never the abbreviation the caller passed: expanding the token is part
    of validating it, and a report that echoed `a1b2c3d` back would be quoting
    the argument instead of stating what it resolved to. Part of the caveat.
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
        if self.kind == "commit":
            # 🔴 THE WORDING IS THE DELIVERABLE, and this window is a THIRD
            # thing: not a session (it does not know who authored the commit)
            # and not a branch (a sibling commit on the same branch is NOT in
            # it). Both of the other caveats would be wrong here in a different
            # direction, which is why this is a branch and not a reuse.
            listed = ", ".join(s[:12] for s in self.commits)
            return (
                f"commit(s) {listed}: every file THOSE COMMITS changed, each diffed "
                f"against its own parent — what THESE COMMITS CHANGED, which is "
                f"neither a SESSION nor a BRANCH. It EXCLUDES uncommitted work, and "
                f"anything done that never became one of these commits: an edit later "
                f"reverted or amended away, and every commit not named here — a "
                f"SIBLING commit on the same branch is NOT in this window. It can "
                f"WRONGLY INCLUDE more than the work being recorded: a commit that "
                f"also carried a formatting sweep, a bulk rename, a squash of hunks "
                f"someone else wrote, or a file swept in by a wide `git add`, is "
                f"reported WHOLE — this source reads the diff and cannot see intent. "
                f"Attribute these paths to THESE COMMITS, never to a session and "
                f"never to a branch"
            )
        if self.window == "session-absolute":
            # 🔴 A FIFTH caveat, not a footnote on the session one. The session
            # caveat's "relative to the session cwd" is the exact claim this
            # window cannot make — its cwd is another repo — and its blind spots
            # are a strict superset: everything the session window misses, plus
            # every path this session named RELATIVELY, which here is not a
            # counted remainder but an unknowable one.
            return (
                f"session transcript {self.session}, ABSOLUTE-PATH window: this session "
                f"ran in {self.session_cwd or '(no cwd recorded)'}, NOT this repo, so the "
                f"only paths reportable here are the ones its turns named as ABSOLUTE "
                f"paths that resolve UNDER this repo. Nothing was re-anchored: a path this "
                f"session named RELATIVELY belongs to ITS cwd and is EXCLUDED, not counted "
                f"— the transcript does not say which tree it meant, so how much work is "
                f"missing is UNKNOWN, not merely unlisted. This is a FLOOR on what the "
                f"session did here, never a complete list. NOT represented, additionally: "
                f"(a) anything a SUBAGENT edited — its turns are excluded as a separate "
                f"session; (b) files written by a Bash command rather than a file tool; "
                f"(c) every absolute path in any OTHER tree. Attribute these paths to this "
                f"SESSION but read the count as a lower bound"
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


def _toplevel(repo: str | Path) -> Path:
    """The repo ROOT for a directory that may be anywhere inside it. Runs git.

    🔴 ONE FRAME, ONE CALL SITE. This used to be open-coded at four of them —
    `scope_for_repo`, and each source that has to fix a path frame — and the
    mutation harness is what surfaced it: the anchor for the git source's frame
    guard started matching TWICE the moment a second source needed the same two
    lines, so the mutant that had been pinning it could no longer be applied at
    all. `claude/RULES.md` → "One rule, one place": a predicate open-coded at N
    sites is typically wrong at N−1 of them.

    Why it matters is stated where it bites hardest, in `collect_git_paths`:
    `diff`/`diff-tree` are always repo-root-relative while `ls-files --others` is
    cwd-relative AND cwd-scoped, so a caller passing a subdirectory would get two
    different frames in one path set — components both manufactured and lost.
    """
    return Path(_git(Path(repo), ["rev-parse", "--show-toplevel"]).strip())


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
    # 🔴 One frame for every command below. See the docstring, and `_toplevel`.
    repo = _toplevel(repo)
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
    # ⚠ KNOWN ASYMMETRY WITH THE OTHER HALF OF GUARD 4, stated rather than left
    # to be discovered. This function resolves symlinks; the absolute window it
    # falls through to (`changed_paths.absolute_under`) matches purely
    # LEXICALLY, for the extractor's own reason — a `realpath` pass would make
    # the answer depend on the filesystem at read time rather than on the
    # transcript. So a repo reachable by two spellings can be "the same
    # directory" here and not-under-the-root there, and the refusal's
    # "NONE of the paths it named are absolute paths under this repo" would then
    # be false about a path that is under it by another name.
    #
    # NOT LIVE on either host — no managed path here resolves through a symlink
    # into a repo root — and both errors point the safe way (under-report, never
    # invent). Closing it means choosing ONE frame for the whole guard, which is
    # a change to the shared extractor's stated policy and does not belong in a
    # local fix. If a session ever reports a surprising empty window, look here.
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
      4. its cwd IS this repo …         TranscriptCwdMismatchError
         …or it named ABSOLUTE paths under this repo, which is the OTHER window
         this function returns (`window="session-absolute"`). (4) raises only
         when BOTH are false.

    (3) must precede (4): an unreadable transcript yields `cwd == ""`, so a cwd
    check placed first would fire for it and the unreadable case's own test would
    pass on a neighbour's error — green with the guard it claims to test deleted.

    🔴 TWO WINDOWS, ONE TRANSCRIPT, DIFFERENT CAVEATS. A cwd match gives the full
    session window. A mismatch gives the absolute subset — a FLOOR, since the
    session's relative paths name a tree the transcript does not identify — and
    the two must never be described in each other's words. See `PathSource.window`.

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
    #
    # The repo frame is resolved BEFORE the read, because the extractor needs it:
    # the absolute window (guard 4) is computed inside the extractor from the raw
    # path set, which never leaves it. This moves a `git rev-parse` ahead of
    # guards 3–4 and nothing else — the CWD COMPARISON, which is what the guard
    # order is about, still happens below, after the readability verdict.
    toplevel = str(_toplevel(repo))
    rollup = tailer.summarize_transcript(str(path), absolute_root=toplevel)
    observed = rollup.get("changed_paths")
    if rollup.get("unreadable") or observed is None:
        raise TranscriptUnreadableError(
            f"transcript unreadable: {path} yielded no readable session — the file set "
            f"is UNKNOWN, which is not the same as empty, so nothing is reported rather "
            f"than an empty window that reads as 'this session touched nothing'"
        )

    # --- guard 4: it is THIS repo, or the ABSOLUTE subset that provably is ------
    #
    # 🔴 THE GUARD IS A FRAME CHECK, NOT A PROVENANCE CHECK — and stating it as
    # the latter is what made it over-refuse for months. The extractor's DEFAULT
    # window reads every entry against the session's cwd, so when that cwd is
    # another repo the RELATIVE entries are repo-relative to the wrong tree and
    # would resolve here silently and plausibly. That half is unchanged and must
    # stay: `src/a.py` in session-cwd A and `src/a.py` under repo B are unrelated
    # strings that happen to spell the same thing.
    #
    # What was false is the generalisation. A transcript entry is the tool call's
    # own `file_path`, which is ABSOLUTE whenever the caller passed an absolute
    # path — and an absolute path that resolves under THIS repo needs no
    # inference to attribute, whatever cwd the session ran in. Those paths were
    # discarded by a refusal whose stated reason — "every path in a transcript is
    # relative to the session's own cwd" — was not true of them.
    #
    # 🔴 THE YIELD IS ~30 CROSS-PROJECT PATHS, NOT THE 112 THAT MOTIVATED THIS.
    # The 112 was an over-count and is RETRACTED — do not re-derive it. It came
    # from treating every top-level directory under ~/workspace as a distinct
    # project, so devrc's own sibling WORKTREE directories (`devrc-fix443`,
    # `devrc-clickup`, `devrc-clawgate-ext`, …) each counted as "another repo".
    # Reconciled on the recent-120 window:
    #
    #     185  absolute, outside cwd, under ~/workspace
    #     -97  the SAME repo reached from one of its worktrees
    #     = 88  in a different top-level directory
    #     -58  of which are devrc-* sibling worktrees, still the same project
    #     = 30  genuinely cross-project (24 homelab->civit, 6 homelab->devrc)
    #
    # Independently corroborated here over the 636 transcripts `iter_transcripts`
    # walks: 3,913 distinct paths, 543 under cwd (13.9%, matching the extractor's
    # own 14.3% figure), 33 absolute under another ~/workspace repo — 29 distinct
    # (repo, path) pairs. Same order as 30, by a different script.
    #
    # 🔴 THE REWRITE DOES NOT REST ON THAT NUMBER AT ALL. It rests on the
    # refusal's stated reason being FALSE, which needs no yield figure. The
    # same-repo-from-a-worktree case above is worth its own note: those 97 are
    # not cross-project, but they were ALSO refused, and this window answers them
    # too — which is where most of the practical benefit on this host actually
    # comes from.
    #
    # So a cwd mismatch now falls through to the absolute window instead of
    # refusing outright, and refuses only when that window is EMPTY, which is the
    # case the original message actually described.
    session_cwd = rollup.get("cwd") or ""
    if not _same_dir(session_cwd, toplevel):
        absolute = rollup.get("changed_paths_absolute") or []
        # 🔴 ONE ACCOUNTING, QUOTED BY BOTH EXITS. This guard leaves two ways —
        # a refusal and a window — and they owed the reader different numbers:
        # the refusal named all three counters, the note named two, and the
        # dropped one (`outside_cwd`) meant a reader adding up the note's figures
        # reached a SMALLER session than the one on disk. Built here, once, so
        # the two cannot drift apart again.
        #
        # `total` and `outside` PARTITION the distinct set — the extractor pins
        # `total + outside_cwd == files_modified` — so they are safe to add.
        # `abs_total` is NOT a third part: it re-reads paths already counted in
        # one of those two, under a different root, and in the nested case (a
        # session cwd inside the repo, or the reverse) it can overlap EITHER.
        # Hence the explicit warning rather than a subtraction that would be
        # wrong exactly when someone most wants the number.
        abs_total = rollup.get("changed_paths_absolute_total")
        total = rollup.get("changed_paths_total")
        outside = rollup.get("changed_paths_outside_cwd")
        distinct = rollup.get("files_modified")
        accounting = (
            f"of {distinct} distinct path(s) this session named, "
            f"{total} path(s) expressible relative to that cwd and {outside} outside it"
        )
        if not absolute:
            raise TranscriptCwdMismatchError(
                f"transcript cwd does not match: session {label} ran in "
                f"{session_cwd or '(no cwd recorded)'}, but --repo resolves to {toplevel}, "
                f"and NONE of the paths it named are absolute paths under this repo — "
                f"{accounting}, and every one of them belongs to a tree this repo cannot "
                f"claim; re-anchoring them here would associate another repo's work with "
                f"this one. "
                f"USE A DIFFERENT SOURCE, not a different uuid: the session ran elsewhere, "
                f"so this repo's git window is empty too. Re-run with --pr <n>[,<n>...] or "
                f"--commit <sha>[,<sha>...] over what you landed here."
            )
        paths, dropped = _filter_excluded(absolute, exclude)
        notes = [
            f"{abs_total} path(s) named ABSOLUTELY by this session and resolving under "
            f"{toplevel} — that is the window below. The session itself ran in "
            f"{session_cwd or '(no cwd recorded)'}: {accounting}. The first group is "
            f"EXCLUDED here, because a relative path names no tree. 🔴 The two groups "
            f"partition the {distinct}; the {abs_total} above OVERLAPS them — it re-reads "
            f"paths already counted there, under this repo's root — so do not add it in"
        ]
        if rollup.get("changed_paths_absolute_truncated"):
            notes.append(
                f"🔴 TRUNCATED at the extractor's cap of {rollup.get('changed_paths_cap')} "
                f"— the list is a lexicographic PREFIX of "
                f"{rollup.get('changed_paths_absolute_total')} paths, so a late-sorting "
                f"subtree may be missing entirely"
            )
        if dropped:
            notes.append(_exclusion_note(dropped))
        notes.append(f"transcript: {path}")
        return PathSource(
            kind="session",
            window="session-absolute",
            paths=tuple(paths),
            commands=(("git", "rev-parse", "--show-toplevel"),),
            notes=tuple(notes),
            session=label,
            session_cwd=session_cwd,
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


# --- The commit source: paths from what THESE COMMITS changed ------------------
#
# 🔴 READ `PathSource.caveat` FOR WHAT THIS SET IS BEFORE READING WHAT IT DOES.
# It answers "what did THESE COMMITS change" — a third question again, neither
# the session's nor the branch's.
#
# WHY A COMMIT SOURCE RATHER THAN A FOURTH WORKFLOW PATCH
# -------------------------------------------------------
# The other two windows are blind BY CONSTRUCTION in the repos where the store's
# entries actually accrue, and both blind spots were measured rather than
# supposed (2026-08-12, on the repo that holds 23 of the store's 24 entries;
# unnamed here because this repo is PUBLIC):
#
#   * SESSION. That repo's own `CLAUDE.md` forbids committing in the primary
#     clone and mandates a throwaway worktree under `/tmp/wt-*`. Every edit
#     therefore lands outside the session cwd and has NO repo-relative form
#     against `--repo`: a real run reported 25 paths outside the cwd and 0
#     inside, i.e. `looked-at-nothing`. The transcript is not wrong; the paths
#     genuinely do not belong to that directory.
#   * PR. Over that repo's last 200 mainline commits, 56 carry a `(#N)` suffix and
#     144 do not — so 72% of what lands never passes through a pull request and
#     `--pr` cannot see it at all.
#
# A COMMIT IS THE PRIMITIVE THE OTHER SOURCES REDUCE TO. A PR is a set of
# commits; worktree-authored work becomes a mainline commit; a direct push IS a
# commit. So this source is workflow-agnostic BY CONSTRUCTION rather than by
# luck — which is the property a fourth per-workflow special case would not have.
# What it costs is that the shas must come from the caller: nothing on the
# machine knows which commits an agent just created, and `/handoff` step 4 says
# so in those words.
#
# 🔴 AND IT STILL DOES NOT COMPOSE WITH THE OTHERS. Same reason as `--session` vs
# `--pr`, one flag further along: a union carries ONE caveat sentence over a set
# whose members need three different ones. Run it separately.

COMMIT_SHA_MIN_CHARS = 4
"""Git's own floor for a human-typed abbreviation, and not a style choice: at 3
chars `rev-parse --disambiguate` still EXPANDS the prefix in a small repo
(measured, git 2.55.0), so a typo would resolve to a real commit and be reported
as a deliberate argument."""

COMMIT_SHA_MAX_CHARS = 40
"""SHA-1 hex length. A repo on SHA-256 would need this widened; it is asserted
rather than assumed so the failure is a named refusal, not a wrong window."""

_HEX_DIGITS = "0123456789abcdefABCDEF"


def _hex_sha_token(token: object) -> str:
    """A validated, lower-cased hex sha token, or a named refusal.

    Pure — no git, no repo. Splitting it out is what makes the SHAPE guard
    reachable independently of everything that needs a repository.
    """
    t = str(token).strip()
    # 🔴 TWO GUARDS, NOT ONE CONDITION, because they close different holes and a
    # single `if` would make them one mutation — killable together and therefore
    # neither of them measured. They share a class and a sentinel on purpose:
    # the caller's fix is the same ("pass the sha"), and a second sentinel for a
    # second spelling of "that is not a sha" would be a distinction nothing acts
    # on.
    if not t or not all(c in _HEX_DIGITS for c in t):
        raise CommitRefMalformedError(
            f"commit sha is malformed: {token!r} is not an object name — pass a hex "
            f"sha, e.g. `--commit 4f1eafa` or `--commit 4f1eafa,2ddbc42`. A revision "
            f"EXPRESSION (`HEAD`, `main`, `HEAD~3`, `@{{u}}`) is refused on purpose: "
            f"it resolves against ambient repo state, so the same argument would "
            f"name a different commit on a re-run, in another worktree, or on "
            f"another host."
        )
    if not COMMIT_SHA_MIN_CHARS <= len(t) <= COMMIT_SHA_MAX_CHARS:
        raise CommitRefMalformedError(
            f"commit sha is malformed: {token!r} is {len(t)} characters — pass "
            f"{COMMIT_SHA_MIN_CHARS}-{COMMIT_SHA_MAX_CHARS}. Shorter is refused "
            f"because `rev-parse --disambiguate` still EXPANDS a 3-character prefix "
            f"in a small repo (measured), so a typo would resolve to a real commit "
            f"and be reported as a deliberate argument."
        )
    return t.lower()


def _disambiguate(repo: Path, prefix: str) -> list[str]:
    """Every object in this repo whose name starts with `prefix`. READ-ONLY.

    `rev-parse --disambiguate` is used instead of `rev-parse --verify
    <prefix>^{commit}` for two reasons, both structural:

      * it EXITS 0 and returns a LIST, so "absent", "unique" and "ambiguous" are
        three distinguishable readings rather than one exit code plus a stderr
        string this module would have to pattern-match (the part-heuristic it
        already declares for `gh`, avoided here);
      * `^{commit}` makes git PICK when a prefix names both a blob and a commit.
        Counting all types means the ambiguity guard never inherits git's
        type-peeling tiebreak.
    """
    return _git(repo, ["rev-parse", f"--disambiguate={prefix}"]).split()


def _object_type(repo: Path, sha: str) -> str:
    """`commit`/`blob`/`tree`/`tag`, or `""` when git cannot read the object.

    🔴 RETURNS A VALUE, NEVER RAISES, so "the object is absent" is a first-class
    reading the caller branches on rather than a `GitError` whose sentinel would
    be `git command failed` — a true statement about the subprocess and a useless
    one about the sha.
    """
    try:
        return _git(repo, ["cat-file", "-t", sha]).strip()
    except GitError:
        return ""


def _commit_parents(repo: Path, sha: str) -> list[str]:
    """The parent shas of a COMMIT. Call only after the type guard.

    `rev-list --parents -n1` prints `<self> <parent>...`; on a non-commit it
    prints NOTHING at exit 0 (measured), which is why the type guard has to run
    first — here it would read as "a root commit", the opposite of an error.
    """
    out = _git(repo, ["rev-list", "--parents", "-n", "1", sha]).split()
    return out[1:]


def _commit_reachable(repo: Path, sha: str) -> bool:
    """Is this commit contained in any ref? Reported, never enforced. READ-ONLY."""
    return bool(
        _git(repo, ["for-each-ref", "--contains", sha, "--count=1", "--format=%(refname)"]).strip()
    )


def _resolve_commit(repo: Path, token: object) -> str:
    """One token -> one full 40-char commit sha, or a named refusal.

    🔴 GUARD ORDER IS REACHABILITY ORDER. Each is reached by an input no EARLIER
    guard rejects, so each one's negative control fails on its OWN sentinel
    rather than on a neighbour's:

      1a. the token is HEX                    CommitRefMalformedError
      1b. …and 4-40 characters long           CommitRefMalformedError
      2.  the prefix names ONE object         CommitAmbiguousError
      3.  …and it names at least one          CommitMissingError
      4.  that object is a COMMIT             CommitWrongTypeError
      5.  …with at most one parent            CommitIsMergeError

    1a and 1b are two `if`s and not one condition: they close different holes
    (an expression that resolves vs a typo that expands), and one combined `if`
    would die to one mutation, leaving neither half measured. They share a class
    and a sentinel because the caller's fix is the same either way.

    (4) must precede (5): `rev-list --parents` on a blob prints nothing at exit
    0, which this code would read as "no parents" — a root commit — so a merge
    check placed first would silently pass every blob.

    (2) must precede (3) rather than the other way round because they read the
    SAME list: `[]` is absent and `[a, b]` is ambiguous, and collapsing them
    would make an ambiguous prefix report `commit not found` — a diagnosis that
    sends the caller looking for a commit that is right there.
    """
    t = _hex_sha_token(token)

    # --- guards 2 and 3: exactly one object answers to this name ----------------
    candidates = _disambiguate(repo, t)
    if len(candidates) > 1:
        raise CommitAmbiguousError(
            f"commit sha is ambiguous: `{t}` names {len(candidates)} objects in this "
            f"repo ({', '.join(candidates)}) — pass the full 40-character sha. It is "
            f"refused rather than resolved because picking one would inherit git's "
            f"type-peeling tiebreak, and you already have the exact sha in hand."
        )
    # 🔴 A VALUE, THEN A GUARD ON IT — not `candidates[0]` behind an `if`. With
    # the subscript inside the guarded branch, deleting the guard produces an
    # IndexError rather than a wrong ANSWER, so the mutation would "kill" on a
    # crash and prove nothing about what the absent guard lets through.
    full = candidates[0] if candidates else ""
    if not full:
        raise CommitMissingError(
            f"commit not found: no object in this repo is named `{t}`. Either it does "
            f"not exist, or it was created in ANOTHER repository — nothing local can "
            f"tell those two apart, and both mean the same thing here: there is no "
            f"diff to read. (A commit made in a WORKTREE of this repo IS present; "
            f"worktrees share one object database.)"
        )

    # --- guard 4: it is a commit -------------------------------------------------
    otype = _object_type(repo, full)
    if otype != "commit":
        raise CommitWrongTypeError(
            f"object is not a commit: `{t}` names a {otype or 'unreadable object'} "
            f"({full}). This is refused rather than diffed because `git diff-tree` on "
            f"a non-commit EXITS 0 with an empty file list and complains only on "
            f"stderr — so it would be reported as a commit that changed nothing."
        )

    # --- guard 5: it is not a merge ---------------------------------------------
    parents = _commit_parents(repo, full)
    if len(parents) > 1:
        raise CommitIsMergeError(
            f"commit is a merge: {full} has {len(parents)} parents "
            f"({', '.join(p[:12] for p in parents)}), so it has no single diff — and "
            f"`git diff-tree` prints NOTHING for one, at exit 0. Pass the side "
            f"commits it merged, or use `--pr <n>`, which is the source built for "
            f"what a BRANCH landed and carries that caveat."
        )
    return full


def collect_commit_paths(
    repo: str | Path,
    shas: Iterable[object],
    *,
    exclude: Iterable[str] = (),
) -> PathSource:
    """What the named COMMITS changed. Per-commit — neither per-session nor per-branch.

    The window the other sources cannot express in a repo that does not land its
    work through pull requests, or that mandates committing from a throwaway
    worktree; see the section comment above for both measurements.

    🔴 `--root` IS NOT OPTIONAL. `git diff-tree <root-commit>` prints NOTHING
    without it (measured) — the first commit in a repo would report having
    changed no files, which is the silent zero this whole module exists to
    refuse. With it, a root commit reports the files it introduced.

    🔴 AN EMPTY COMMIT IS A READING, NOT AN ERROR, matching the PR source's
    treatment of an empty `files` list. A `--allow-empty` commit is well-formed
    and genuinely changed nothing; its `0 file(s)` is stated in a note, on every
    run, so the zero is accounted rather than merely absent. Refusing it would
    also break composition — one empty sha in `--commit a,b,c` would kill an
    otherwise good run.

    🔴 AN UNREACHABLE COMMIT IS ACCEPTED, and that is a decision. A commit created
    in a `/tmp/wt-*` worktree that has since been removed, or one rebased away,
    is reachable from NO ref — and it is exactly the case this flag was built
    for, since the work still happened and the object is still in the shared
    object database. Requiring reachability would reject the motivating case. The
    real consequence — an unreachable object can be garbage-collected, so this
    run is not reproducible later — is REPORTED in the per-commit note rather
    than turned into a refusal.
    """
    # One frame for every command, through the SHARED resolver: `diff-tree` is
    # repo-root-relative wherever it runs, but recording an argv that only
    # reproduces from a subdirectory would make `commands` a line a reader
    # cannot re-run.
    repo = _toplevel(repo)

    commands: list[tuple[str, ...]] = []
    notes: list[str] = []
    raw: list[str] = []
    # Named `resolved` rather than `read` deliberately: it holds the EXPANDED
    # 40-char shas, so `full in resolved` recognises one commit named two ways
    # (`a1b2c3d` and its own full form) as one. Deduping on the token would read
    # it twice and double the per-commit accounting a reader uses to check the
    # window.
    resolved: list[str] = []

    for token in shas:
        full = _resolve_commit(repo, token)
        if full in resolved:
            notes.append(f"commit {full[:12]} was named more than once; read once")
            continue
        resolved.append(full)
        args = ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", full]
        commands.append(("git", *args))
        paths = _nul_list(_git(repo, args))
        raw.extend(paths)
        # 🔴 EMITTED UNCONDITIONALLY, INCLUDING AT ZERO. A stated `0 file(s)` is a
        # reading; an absent line is indistinguishable from a differ wired to
        # nothing (`claude/RULES.md` → "report the pair"). The reachability word
        # is on the same line for the same reason — it prints BOTH values, so
        # `unreachable` is never the only thing anyone has seen it say.
        where = "reachable from a ref" if _commit_reachable(repo, full) else (
            "NOT reachable from any ref — accepted (a throwaway-worktree or "
            "rebased-away commit is still work that happened), but it can be "
            "garbage-collected, so this run is not reproducible later"
        )
        notes.append(f"commit {full[:12]}: {len(paths)} file(s) changed; {where}")

    if not resolved:
        # Reachable via `--commit ,` or `--commit ''`. Falling through would
        # return a perfectly well-formed report over ZERO commits — the confident
        # zero, arriving through argument parsing.
        raise CommitMissingError(
            "commit not found: no commit sha was given, so nothing was read. "
            "`--commit` takes one or more hex shas, e.g. `--commit 4f1eafa,2ddbc42`."
        )

    paths, dropped = _filter_excluded(raw, exclude)
    notes.append(f"{len(paths)} distinct path(s) across {len(resolved)} commit(s)")
    if dropped:
        notes.append(_exclusion_note(dropped))

    return PathSource(
        kind="commit",
        window="commits",
        paths=tuple(paths),
        commands=tuple(commands),
        notes=tuple(notes),
        commits=tuple(resolved),
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

    policy_path: str | None = None
    """WHICH policy file governs a write into this scope — `governing_policy`'s
    answer, carried on the report so the renderer and the JSON quote ONE result.

    🔴 It is on the PROBE's report because the probe is what runs before the
    write. `handoff/SKILL.md` step 4 tells the writer to read the scope's README
    and calls it authoritative, and measured 2026-08-13 only 1 of 5 scopes has
    one — so the instruction was unfollowable 80% of the time, with no signal
    that the file was simply absent rather than unread. Naming the file that
    actually governs is deterministic and invents no policy; generating a README
    into a scope would be manufacturing authority, and is deliberately not done.
    """

    policy_basis: str = POLICY_NONE
    """Which of the three cases `policy_path` is — a scope README, the store-root
    fallback, or neither. The path alone cannot say whether the reader is looking
    at policy written FOR this scope, and mistaking the fallback for the scope's
    own is believing a scope has spoken when it has not."""

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
    # 🔴 RESOLVED ONCE, HERE, AND CARRIED ON EVERY RETURN — including
    # `looked-at-nothing`, which is where a session with an empty window still
    # goes on to consider a NEW entry and therefore still needs to know which
    # policy file governs. Deriving it per branch would be the same predicate at
    # three sites.
    policy_path, policy_basis = governing_policy(store, scope)
    if not source.paths:
        return TouchReport(
            status="looked-at-nothing",
            scope=scope,
            store_root=str(store),
            source=source,
            today=today,
            min_paths=min_paths,
            policy_path=policy_path,
            policy_basis=policy_basis,
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
    # 🔴 THE WRITER STAYS FAIL-CLOSED, AND THAT IS DELIBERATE. The READER
    # (`subsystem_recall`) degrades — it serves what it can and reports the rest —
    # because spending every good entry to report one bad one is a bad trade for
    # somebody who only wants to look. This is the other side: it gates a WRITE
    # into a curated, unbacked-up store, and acting on a partially-read index is
    # the one case where aborting is cheaper than degrading. The exit code is
    # unchanged (3).
    #
    # ⚠ THE CLAUSE USED TO BE A LABELLED NO-OP. `MalformedEntryError` is a
    # `ResolverError`, not an `OSError`, so `except MalformedEntryError: raise`
    # sitting above the `OSError` clause could never change an outcome, and a
    # mutation test proved it by refusing to kill it. It is LOAD-BEARING now: it
    # rewords the refusal so it names the way out. Do not restore the bare
    # re-raise — `test_the_refusal_NAMES_the_recovery` is what stops that.
    try:
        index = load_index(store)
    except MalformedEntryError as exc:
        # Re-raised as the SAME class with the SAME sentinel leading the message,
        # so every existing `except`, every `in str(exc)` assertion and the exit
        # code all keep working. `from exc` preserves the original for a
        # traceback; what changes is only what the operator reads.
        raise MalformedEntryError(
            malformed_refusal(store, scope, exc), source=exc.source, why=exc.why
        ) from exc
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
            policy_path=policy_path,
            policy_basis=policy_basis,
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
        policy_path=policy_path,
        policy_basis=policy_basis,
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
# 🔴 THE ROUTE OUT OF A DEAD END. A window that resolved nothing is a fact about
# THE WINDOW READ, never about the session — and the four windows are blind in
# different directions, so the agent's next move is to read a different one. The
# tool knows which one it just ran and the alternatives are a closed set, so it
# can say this deterministically instead of leaving the skill to describe it in
# prose and the agent to remember at the one moment it is being told "no".
#
# MEASURED, 2026-08-14, the incident this exists for: a session in a repo whose
# rules force every edit into a throwaway worktree got `looked-at-nothing` from
# `--session` (0 paths under the session cwd, 12 outside it — structural there,
# not occasional), correctly fell through to `--commit` over the shas it had
# made, and got `no-match` over ONE `claudedocs/` path from five commits. It
# reported that as "a real zero, index unchanged, correctly". The zero was real
# for the window read and the window was the wrong one: that session's work was
# committed inside the worktree and landed as a PR, so the base-clone shas were
# just the handoff doc. `--pr` — the only source that sees it — was never tried,
# and nothing on screen named it. 🔴 The trigger was reading "worktree" as "use
# `--commit`"; the real discriminator is whether the work LANDED AS A PR, and a
# worktree goes both ways (in the repo holding most of this store, 144 of its
# last 200 mainline commits carry no `(#N)`, so `--commit` IS right there).
# Which is why this block names every unread window rather than picking one.
#
# The mapping is deliberately NOT a recommendation engine. It states which
# windows exist and which was read; choosing among them stays with the agent,
# which is why every line carries what that source uniquely sees rather than a
# score.
# 🔴 Keyed by SOURCE, not by matching the flag's spelling. An earlier draft
# excluded the used source by comparing the first token of two display strings,
# which silently failed for the git window (`(git, the default)` vs `(no flag)`
# share no prefix) — so the git window would have suggested itself. A window maps
# to a source; a source has one row. No string arithmetic.
_WINDOW_SOURCE: dict[str, str] = {
    "session": "session",
    "session-absolute": "session",
    "branch": "git",
    "worktree": "git",
    "pull-requests": "pr",
    "commits": "commit",
    # `supplied` is the in-process/test entry point and belongs to no flag, so
    # it excludes nothing: all four real windows genuinely are untried.
    "supplied": "",
}

# (source, flag, what it UNIQUELY sees). The `why` is what the source can see
# that the others cannot — never a score, because choosing among them is the
# agent's call and this block is a statement of fact, not a recommendation.
_ROUTE_OUT: tuple[tuple[str, str, str], ...] = (
    (
        "pr",
        "--pr <n>[,<n>...]",
        "the only source that sees a SUBAGENT's work, and the only one that "
        "sees work committed inside a throwaway worktree AND landed as a PR",
    ),
    (
        "commit",
        "--commit <sha>[,<sha>...]",
        "work that became a commit but no PR — a direct push, or a branch not "
        "opened yet",
    ),
    (
        "session",
        "--session <uuid>",
        "what THIS session's own turns edited, independent of git — blind to a "
        "subagent's work and to anything outside the session cwd",
    ),
    (
        "git",
        "(no flag)",
        "the git branch/worktree window — blind to work already merged",
    ),
)

_SOURCE_FLAG: dict[str, str] = {src: flag for src, flag, _ in _ROUTE_OUT}


# 🔴 THE OTHER DEAD END: the lesson is real and the INDEX is the wrong home.
#
# The store takes SUBSYSTEM entries. A session also produces durable ops-gotchas
# that belong in `claude/skills/<name>/SKILL.md` instead, and the handoff skill
# says so explicitly — but it says it as a REASON TO DECLINE the index write, and
# nothing then routes the lesson anywhere. Declining costs one sentence; filing it
# costs finding the owning skill, writing under the always-on listing budget, and
# `git add`ing a file the flake otherwise silently omits. So the cheap path is the
# lossy one, and the knowledge ends its life as prose in a transcript — the exact
# medium this store exists to outlive.
#
# MEASURED 2026-08-14, the incident this exists for: a session correctly declined
# both windows, correctly concluded the gotchas belonged in a skill, named
# `.claude/skills/pyroscope/SKILL.md`, and stopped. That skill DOES NOT EXIST —
# `obs-read` already owns Pyroscope, in its description. So the note would have
# created a duplicate skill and split the domain, and the gotcha it carried
# (Pyroscope's `max_query_length: 1d`) was recorded nowhere.
#
# 🔴 THIS CANNOT KNOW THE DOMAIN, AND MUST NOT PRETEND TO. The domain term came
# from what the session was DOING ("pyroscope"), which need not appear in any path
# it touched. So this does two separate things and labels which is which: it
# matches terms it can actually derive (path stems + the scope), and it prints the
# exact command for the term only the agent has. A hit is a lead; an empty result
# is NOT "no skill owns this".
#
# Matching is against each skill's NAME and DESCRIPTION only, never the body: the
# description IS the routing surface (it is what the always-on listing carries),
# and matching bodies would return half the catalogue for a common word.
SKILLS_ROOT_DEFAULT = "~/.claude/skills"

# Tokens that appear in paths everywhere and would match a skill by accident.
# Deliberately short: over-filtering loses the lead, and every hit is printed with
# the term that produced it so the agent can dismiss a bad one in one glance.
_TERM_STOPLIST = frozenset({
    "test", "tests", "main", "index", "docs", "doc", "readme", "handoff",
    "claudedocs", "script", "scripts", "config", "default", "init", "utils",
    "lib", "src", "reference", "skill", "types", "common", "helpers",
})


def _terms_from(paths: _abc.Iterable[str], scope: str) -> tuple[str, ...]:
    """Lower-cased candidate domain terms from the WHOLE path + the scope.

    🔴 The whole path, not the basename. An earlier draft took
    `os.path.basename` and threw away every DIRECTORY component — which is
    where the domain usually lives. Caught by a smoke test on the very case
    this feature exists for: `src/pyroscope/query.go` yielded only `query`
    and `go`, so the one right answer (`obs-read`, via `pyroscope`) was not
    merely ranked low, it was ABSENT from the candidate terms entirely.
    """
    out: dict[str, None] = {}
    for raw in (scope, *paths):
        stem = str(raw)
        for sep in ("/", "\\", ".", ":"):
            stem = stem.replace(sep, " ")
        for part in stem.split():
            # 🔴 The WHOLE component as well as its split pieces. A skill named
            # for a compound owns the compound: `scripts/repo-cos/scan.py` split
            # only into `repo` + `scan`, so `repo-cos` matched on the generic
            # `scan` (breadth 3) and ranked FOURTH behind three scan-ish skills —
            # inside a cap of 4 by one place. As its own term it has breadth 1
            # and ranks first, which is the whole point of specificity ranking.
            pieces = [p.strip().lower() for p in
                      part.replace("-", " ").replace("_", " ").split()]
            keep = [p for p in pieces
                    if len(p) >= 4 and p not in _TERM_STOPLIST and not p.isdigit()]
            # The compound rides on its pieces: `repo-cos` survives because
            # `repo` does, but `test_index` must NOT — both of ITS pieces are
            # stoplisted, and letting the compound through re-admitted exactly
            # the noise the stoplist exists to remove. Caught by the knob test.
            cand = ([part.strip().lower()] if keep and len(pieces) > 1 else []) + keep
            for tok in cand:
                if len(tok) >= 4 and tok not in _TERM_STOPLIST and not tok.isdigit():
                    out.setdefault(tok, None)
    return tuple(out)


def skill_catalogue(skills_root: str | Path | None = None) -> tuple[tuple[str, str], ...]:
    """(name, description) for every deployed skill. READ-ONLY, no subprocess.

    Absent root is ORDINARY, not an error — a host without the skills deployed
    simply has no leads to offer, and raising here would turn a helpful extra
    into a reason the whole dead-end report fails.
    """
    root = Path(os.path.expanduser(str(skills_root or SKILLS_ROOT_DEFAULT)))
    # 🔴 The WALK is inside the try, not just the parse. An `if not root.is_dir()`
    # pre-check is TOCTOU — a concurrent `home-manager switch` can remove the
    # directory between the check and `iterdir()` — and an unreadable skill dir
    # raises PermissionError from `iterdir`/`is_dir`/`is_file`, none of which a
    # parse-only try would catch. `main` renders OUTSIDE its TouchError handler,
    # so either escape surfaces as a traceback and the dead-end report — the
    # thing the handoff protocol keys on — never prints at all. This helper is an
    # EXTRA; it must never be why the report fails.
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return ()
    found: list[tuple[str, str]] = []
    for entry in entries:
        try:
            md = entry / "SKILL.md"
            if not md.is_file():
                continue
            fm = parse_front_matter(md.read_text(encoding="utf-8", errors="replace"))
        except (OSError, Exception):
            # A malformed or unreadable skill must not take the report down with
            # it — the same degrade-don't-die rule the reader learned from a
            # wrapped `aliases:`.
            continue
        desc = fm.get("description")
        found.append((entry.name, desc if isinstance(desc, str) else ""))
    return tuple(found)


def skill_homes(
    terms: _abc.Iterable[str], skills_root: str | Path | None = None
) -> tuple[tuple[str, str], ...]:
    """(skill, term-that-matched), MOST DISCRIMINATING TERM FIRST.

    🔴 Ranked by term specificity, and it has to be. Unranked, the real lead
    drowns: measured on this host, a path carrying `pyroscope` also yields the
    terms `query` and `homelab`, which match FOUR and THREE other skills
    respectively (re-measured 2026-08-14 on the live 34-skill catalogue; an
    earlier draft of this comment said "2 each" and was wrong) — so `obs-read`,
    the one right answer, came out FOURTH and a cap of 4 was one generic term
    away from hiding it entirely. A term matching one skill is a lead; a term matching
    six is a coincidence.

    On a BREADTH TIE the LONGER term wins, then the skill name. Measured on the
    fixture: `pyroscope` and `homelab` each match exactly one skill, so a plain
    alphabetical tie-break ranked `mailbox` above `obs-read` — the one right
    answer, second, on a coin flip. At equal breadth the longer term carries more
    information; the name only breaks a true tie, so the order stays deterministic.
    """
    terms = tuple(terms)
    catalogue = skill_catalogue(skills_root)
    hay_by_skill = {name: f"{name}\n{desc}".lower() for name, desc in catalogue}
    breadth = {
        term: sum(1 for hay in hay_by_skill.values() if term in hay) for term in terms
    }
    hits: list[tuple[int, int, str, str]] = []
    for name, hay in hay_by_skill.items():
        matched = [t for t in terms if t in hay]
        if not matched:
            continue
        best = min(matched, key=lambda t: (breadth[t], -len(t), t))
        hits.append((breadth[best], -len(best), name, best))
    hits.sort()
    return tuple((name, term) for _, _, name, term in hits)


def render_skill_homes(
    paths: _abc.Iterable[str], scope: str, skills_root: str | Path | None = None,
    limit: int = 4,
) -> list[str]:
    """The second route out: where a lesson goes when the INDEX is the wrong home.

    🔴 THE SCOPE IS RANKED LAST, AND WITH NO PATHS IT PRINTS NO ROWS AT ALL.
    The scope is a per-repo CONSTANT, so folding it in with the path terms makes
    the same one or two skills occupy the cap on every dead end in that repo —
    measured on `devrc`, `devrc-dx` and `i3` took two of four slots every time,
    hiding `repo-cos` entirely for `scripts/repo-cos/scan.py` and demoting `bar`
    to third for `scripts/bar-status-poll`. Boilerplate is the failure mode this
    block exists to avoid: a section that prints the same thing regardless of
    input gets skipped, and then it is decoration rather than a route. So
    path-derived hits come first, scope-derived hits fill only what is left, and
    a `looked-at-nothing` run (0 paths, hence no signal whatsoever) prints the
    search command instead of a constant that would look like an answer.
    """
    # Materialised once: `paths` may be a generator, and the empty-vs-no-terms
    # distinction below has to ask about the PATHS, not about the terms.
    paths = tuple(paths)
    path_terms = _terms_from(paths, "")
    scope_terms = tuple(t for t in _terms_from((), scope) if t not in path_terms)
    hits = list(skill_homes(path_terms, skills_root))
    if paths:
        # Scope hits FILL what the path terms left, and only then. With no paths
        # at all the scope is the only term available and every dead end in the
        # repo would print the same rows — a constant that reads as an answer.
        # (A test caught this: the first draft guarded only the MESSAGE branch
        # and still emitted scope rows, which made the docstring above false.)
        seen = {name for name, _ in hits}
        hits += [h for h in skill_homes(scope_terms, skills_root) if h[0] not in seen]
    lines = [
        "",
        "SKILL HOMES — if the lesson is a durable ops-gotcha rather than a "
        "subsystem, the index is the wrong home and a skill is the right one.",
    ]
    if hits:
        for name, term in hits[:limit]:
            lines.append(f"    claude/skills/{name}/SKILL.md   (matched {term!r})")
        if len(hits) > limit:
            lines.append(f"    … and {len(hits) - limit} more; these are LEADS, not answers.")
    elif not paths:
        lines.append(
            "    (no paths were examined, so there is NOTHING to derive a term from — "
            "this is an absence of signal, not an absence of a home)"
        )
    else:
        # 🔴 Paths WERE examined and yielded nothing usable — a different fact
        # from "no paths", and keying this branch on the TERMS being empty
        # conflated the two: `a/b.py` has paths but every token is under the
        # length floor, and it printed "no paths were examined", which is false.
        lines.append("    (no skill matched a term derivable from the paths)")
    lines.append(
        "  🔴 Derived from path stems + the scope — NOT from what the session was "
        "ABOUT, which this tool cannot see. An empty result is not 'no skill owns "
        "this'. Search YOUR domain term:"
    )
    lines.append(
        f"    grep -ril '<your-term>' {SKILLS_ROOT_DEFAULT}/*/SKILL.md"
    )
    lines.append(
        "  Edit the REPO source (`claude/skills/…`), never `~/.claude/…` — that is "
        "a read-only store symlink. A NEW file must be `git add`ed or the flake "
        "silently omits it. If nothing owns it, say so as an UNFILED item, with "
        "the term you searched."
    )
    return lines


def render_route_out(window: str) -> list[str]:
    """The untried windows, named, for a run that resolved nothing.

    Excludes the source that produced THIS window: re-running the one that just
    came back empty is the single suggestion that cannot help, and printing it
    would make the block read as boilerplate rather than as a route.
    """
    used = _WINDOW_SOURCE.get(window, "")
    read = _SOURCE_FLAG.get(used, window)
    lines = [
        "",
        "ROUTE OUT — a dead end is a fact about THE WINDOW READ, not about the "
        "session.",
        f"  read: {read}.  The other windows were NOT read:",
    ]
    for src, flag, why in _ROUTE_OUT:
        if src == used:
            continue
        lines.append(f"    {flag:<26} {why}")
    lines.append(
        "  Run at most ONE more, on its own — the windows never compose (see "
        "the skill: run it twice, never merge the two path sets)."
    )
    return lines


def render_text(report: TouchReport) -> str:
    """The agent-facing brief.

    Deterministic in the REPORT: same report in, same bytes out — with one
    documented exception. On a dead end the `SKILL HOMES` block reads the host's
    `~/.claude/skills`, so its rows vary with what is deployed there. That makes
    the two tiers differ by construction: `flake.nix` exports `HOME=$TMPDIR/home`
    for the sandbox and nothing creates `.claude/skills` in it, so CI always
    renders the empty branch and a dev host always renders rows. Never assert on
    those rows from a test that does not inject `skills_root` — the sandbox
    tier is structurally blind to them.
    """
    src = report.source
    out: list[str] = []
    out.append(f"subsystem-touch: status={report.status} scope={report.scope}")
    out.append(f"  store: {report.store_root}")
    # 🔴 NAMED, NEVER ASSUMED. The write half of this step is told to read the
    # governing README first; printing WHICH file that is makes the instruction
    # followable in the 4-of-5 scopes that have no README of their own, and makes
    # the third case ("neither exists") a stated fact instead of a silent one.
    out.append(f"  policy: {report.policy_path or '(none)'}  ({report.policy_basis})")
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
        out.extend(render_route_out(src.window))
        out.extend(render_skill_homes(src.paths, report.scope))
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
        out.extend(render_route_out(src.window))
        out.extend(render_skill_homes(src.paths, report.scope))

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
        "policy_file": report.policy_path,
        "policy_basis": report.policy_basis,
        "source": {
            "kind": src.kind,
            "window": src.window,
            "base_ref": src.base_ref,
            "session": src.session,
            "prs": list(src.prs),
            "commits": list(src.commits),
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

    The decision doc's reopening gate is a COUNT, so the instrument for it must
    be a count too — not a recollection of which sessions felt productive. This
    reads the store read-only and reports the raw numbers; it deliberately
    renders no verdict, because the criterion lives in the decision doc and
    restating it here would be the same claim in two places.

    ⚠ That gate TRIPPED and was superseded on 2026-08-13: its original
    entries-outside-one-scope threshold was met, and it was the wrong question
    anyway — the binding constraint is COVERAGE (does the index cover the repos
    where work happens). The per-scope numbers below are still exactly what a
    coverage question reads, so this class is unchanged. Do not copy the new
    criterion here either; see
    `claudedocs/decision-subsystem-store-rejected-2026-08-11.md`.
    """

    total: int
    by_scope: Mapping[str, int]
    by_writer: Mapping[str, int]
    scopes_with_stamped_entries: Mapping[str, Mapping[str, int]]
    # --- activity. See `census()`'s docstring for why a COUNT cannot answer
    # "are the writers still writing", and what these can and cannot see.
    newest_write_epoch: float | None
    touched_within: Mapping[int, int]

    def to_json(self) -> dict:
        return {
            "total": self.total,
            "by_scope": dict(self.by_scope),
            "by_writer": dict(self.by_writer),
            "by_scope_and_writer": {k: dict(v) for k, v in self.scopes_with_stamped_entries.items()},
            "newest_write_epoch": self.newest_write_epoch,
            "touched_within_hours": {str(h): n for h, n in sorted(self.touched_within.items())},
        }


# The activity windows, in hours. 24h answers "is anyone writing today", 168h
# (7d) answers "has this gone quiet" without a single idle weekend reading as
# death.
TOUCH_WINDOWS_HOURS = (24, 168)


def census(store_root: str | Path, now: float | None = None) -> Census:
    """Count entries by scope and by `created_by:`, plus write ACTIVITY. READ-ONLY.

    Front matter is parsed with the resolver's own `parse_front_matter`, not a
    second parser: the live corpus uses an inline flow list and hand-rolled
    quoting that PyYAML would type-coerce, and two parsers over one file format
    is the duplicated-predicate shape again.

    🔴 WHY THE COUNTS ALONE CANNOT DETECT A STALL, which is what they were being
    read for. Every count here is a count of CREATION events: an entry is one
    row forever, and `created_by:` is stamped once at creation and never
    updated. So a writer that spends a week APPENDING to existing entries moves
    none of these numbers, and `total` reads exactly the same whether the store
    is being worked hard or is dead. MEASURED 2026-08-13: the store sat at 34
    across two readings 40 minutes apart while the git history showed 7 new
    entries and 9 appends that same day across 5 scopes. Reading a flat total as
    "the writers are not sticking" would have been precisely backwards.

    So `newest_write_epoch` and `touched_within` are the stall detector, and the
    counts are the coverage instrument. Do not substitute one for the other.

    🔴 WHY mtime AND NOT THE STORE'S GIT LOG. The obvious source is
    `git log` per scope repo, and it is the WRONG one: commits are batched by an
    `analyze-service-index-commit.timer` (`OnCalendar=*-*-* *:00:00` with
    `RandomizedDelaySec=10min`, so the real bound is ~70 min, not 60), a
    git-derived reading lags the actual writes and a mid-hour check can report
    silence during an active session. mtime is what the writer touched, when it
    touched it.

    🔴 AND git DOES NOT ANSWER THE OTHER TWO EITHER — an earlier draft of this
    comment said it was "the only source" for them, which was the same overclaim
    it was written to correct. MEASURED: every commit in every scope carries ONE
    fixed identity (`analyze-service index <analyze-service-index@localhost>`,
    pinned at commit.sh:174), so `git log` cannot attribute a write to a writer
    at all; and the hourly batching above collapses N appends within an hour
    into one commit, so it undercounts them. git is better than mtime at
    counting appends ACROSS hours. That is the whole of its advantage. It also keeps this function subprocess-free and hermetically
    testable with `os.utime`.

    What mtime CANNOT see, and no caller should claim otherwise:
      * HOW MANY times an entry was appended to — a file touched thirty times
        counts once. These are "entries touched", never "writes".
      * WHO touched it. `by_writer` is creation-time attribution and stays that.
      * A restore, SOMETIMES — and which operations reset mtime is not obvious,
        so it is measured here rather than guessed (2026-08-13):
            plain `cp`, `git clone`, `git checkout -- <path>`  -> mtime RESET
                                        (reads as a burst that never happened)
            `cp -a`, `git add`, `git commit`                   -> mtime PRESERVED
        So a fresh clone of the store lies; archiving it aside with `cp -a` does
        not, and neither does the hourly autocommit. A rewrite with IDENTICAL
        content still reads as a touch — `analyze-service`'s confirm flow does
        exactly that.

    `now` is injectable so the windows are testable without sleeping; it
    defaults to wall clock.
    """
    store = Path(store_root)
    if not store.is_dir():
        raise StoreMissingError(f"store root not found: {store}")
    at = time.time() if now is None else now
    by_scope: dict[str, int] = {}
    by_writer: dict[str, int] = {}
    nested: dict[str, dict[str, int]] = {}
    newest: float | None = None
    touched = {h: 0 for h in TOUCH_WINDOWS_HOURS}
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
            mtime = md.stat().st_mtime
            if newest is None or mtime > newest:
                newest = mtime
            for hours in TOUCH_WINDOWS_HOURS:
                # A file stamped in the FUTURE (clock skew, a bad restore) is
                # still "recent" — the alternative is silently dropping it from
                # every window, which reads as quiet.
                if at - mtime <= hours * 3600:
                    touched[hours] += 1
    return Census(
        total=sum(by_scope.values()),
        by_scope=dict(sorted(by_scope.items())),
        by_writer=dict(sorted(by_writer.items())),
        scopes_with_stamped_entries={k: dict(sorted(v.items())) for k, v in sorted(nested.items())},
        newest_write_epoch=newest,
        touched_within=dict(touched),
    )


def _age_phrase(seconds: float) -> str:
    if seconds < 0:
        return "in the future (clock skew or a restored file)"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.1f}d ago"


def render_census(c: Census, now: float | None = None) -> str:
    out = [f"subsystem-touch census: {c.total} entries"]
    out.append("  by scope:")
    for scope, n in c.by_scope.items():
        out.append(f"    {scope}: {n}")
    out.append("  by created_by:")
    for writer, n in c.by_writer.items():
        out.append(f"    {writer}: {n}")
    # 🔴 The counts above are a COVERAGE reading and CANNOT go down or sideways
    # when a writer spends the week appending. Everything below is the stall
    # detector; see census()'s docstring for what it can and cannot see.
    at = time.time() if now is None else now
    out.append("  activity (entries TOUCHED — not writes; an entry appended to 30x counts once):")
    if c.newest_write_epoch is None:
        out.append("    newest write: none — the store holds no entries")
    else:
        out.append(f"    newest write: {_age_phrase(at - c.newest_write_epoch)}")
    for hours, n in sorted(c.touched_within.items()):
        label = f"{hours}h" if hours < 48 else f"{hours // 24}d"
        out.append(f"    touched in the last {label}: {n}")
    out.append(
        "    (mtime-derived, deliberately NOT the store's git log — commits are "
        "batched hourly with 10m of jitter, so a git reading lags real writes by ~70m)"
    )
    out.append(
        "  (the reopening gate is stated in "
        "claudedocs/decision-subsystem-store-rejected-2026-08-11.md, not here)"
    )
    return "\n".join(out)


# --- Validation: the write-time check -------------------------------------------
#
# 🔴 IT REUSES THE READER'S PARSER AND VALIDATOR — `entry_mapping` +
# `SubsystemEntry.from_mapping` + `load_index(COLLECT)` — and implements no
# second one. A validator that re-derives the rules is a duplicated predicate,
# and its specific failure mode is the worst possible one for a checker: it
# starts blessing entries the reader rejects, which is precisely the situation a
# write-time check exists to prevent. If the schema grows a field, this function
# learns it for free or not at all.
#
# WHY IT EXISTS: a wrapped `aliases:` list was written by one session and
# diagnosed by a different tool in a different session hours later. The confirm
# gate that approved it showed a diff containing the defect while being
# structurally incapable of revealing it — nothing in the loop parsed the bytes.


@dataclass(frozen=True)
class ValidationReport:
    """What `--validate` checked, and what it found. Counts on EVERY path.

    🔴 `checked` IS PRINTED BESIDE `malformed`, ALWAYS. A bare "0 malformed" from
    a scan that walked nothing is indistinguishable from a clean scope, and
    `claude/RULES.md` names that as the failure, not the all-clear: a reassuring
    zero has to carry the size of the thing it is a zero over.
    """

    store_root: str
    target: str
    """What was validated, in words — a file path, or `<scope>/`."""

    scope: str | None
    """The scope, when a whole scope was validated; None for a single file."""

    checked: tuple[str, ...]
    malformed: tuple[MalformedEntry, ...] = ()
    policy_path: str | None = None
    policy_basis: str = POLICY_NONE

    @property
    def clean(self) -> bool:
        return not self.malformed

    def to_json(self) -> dict:
        return {
            "store_root": self.store_root,
            "target": self.target,
            "scope": self.scope,
            "checked": list(self.checked),
            "checked_count": len(self.checked),
            "malformed_count": len(self.malformed),
            "malformed": [
                {"scope": m.scope, "file": m.filename, "reason": m.reason, "line": m.line}
                for m in self.malformed
            ],
            "policy_file": self.policy_path,
            "policy_basis": self.policy_basis,
        }


def validate_entry_file(path: str | Path) -> MalformedEntry | None:
    """Would the loader accept this ONE file? `None` if yes, the rejection if no.

    The scope is the PARENT DIRECTORY NAME, exactly as `load_index` takes it —
    the directory is the authority on scope, so validating against the file's own
    `scope:` field would answer a question the loader never asks.

    ⚠ A single file cannot be checked for DUPLICATES: that is a relationship
    between two files. `validate_scope` covers it, and `render_validation` says so
    on the single-file path rather than leaving a narrower check to look total.
    """
    p = Path(path)
    if not p.is_file():
        raise EntryFileMissingError(
            f"index entry file not found: {p} — `--validate <path>` takes the path to an "
            f"entry `.md`. A missing file is not a malformed one, and reporting it as one "
            f"would send you to fix front matter that is not there"
        )
    mapping = entry_mapping(
        p.read_text(encoding="utf-8", errors="replace"), filename=p.name, scope=p.parent.name
    )
    try:
        SubsystemEntry.from_mapping(mapping, source=p.name)
    except MalformedEntryError as exc:
        return MalformedEntry(
            scope=normalize_ref(p.parent.name), filename=p.name, reason=exc.why
        )
    return None


def validate_scope(store_root: str | Path, scope: str) -> tuple[tuple[str, ...], tuple[MalformedEntry, ...]]:
    """`(checked, malformed)` for every entry file in ONE scope. READ-ONLY.

    Goes through `load_index(COLLECT)` rather than looping `validate_entry_file`,
    so the DUPLICATE-ref check runs too — that rejection lives in `build_index`
    and a per-file loop structurally cannot see it. The `checked` list is taken
    from the directory, not from the index, so a file the loader rejected is
    still counted as examined: `checked` must be "files walked", or the zero it
    accompanies means nothing.
    """
    store = Path(store_root)
    if not store.is_dir():
        raise StoreMissingError(
            f"store root not found: {store} — expected the `/analyze-service` index store; "
            f"nothing was validated, and this is NOT 'the scope is clean'"
        )
    scope_dir = store / normalize_ref(scope)
    checked = (
        tuple(md.name for md in sorted(scope_dir.glob("*.md")) if md.name != "README.md")
        if scope_dir.is_dir()
        else ()
    )
    if not checked:
        return (), ()
    index = load_index(store, on_malformed=ON_MALFORMED_COLLECT)
    return checked, index.malformed_in(scope)


def validate_command(store_root: str | Path, scope: str) -> str:
    """The literal, runnable `--validate` invocation for one scope. ONE spelling.

    🔴 IT IS BUILT, NEVER TYPED INTO PROSE. Every place that tells a caller how to
    recover emits this, so a flag rename cannot leave a skill or an error message
    quoting a command that no longer parses — the exact failure a "just run
    --validate" sentence has no defence against. `Path(__file__)` rather than a
    hardcoded path so a copy of this module names ITSELF and the command stays
    true wherever it is running from.
    """
    return (
        f"python3 {Path(__file__).resolve()} --store {store_root} "
        f"--scope {normalize_ref(scope)} --validate"
    )


def malformed_refusal(store_root: str | Path, scope: str, exc: MalformedEntryError) -> str:
    """The probe's REFUSAL, with the route out of it. Wording only — it still raises.

    🔴 THE PRECEDENT IS `TranscriptCwdMismatchError` (#436), IN THIS SAME SKILL:
    the guard was right to refuse, and the fix was to make the refusal NAME THE
    ALTERNATIVE instead of stopping at the hazard. This message stopped at the
    hazard — `malformed index entry 'bad.md': ...` and nothing else — while
    `handoff/SKILL.md` step 4 tells the agent that a non-zero exit means "print
    the stderr line verbatim and write NOTHING". So the agent got a dead end: no
    recovery, no way to tell whether the offending file was the entry it was
    about to touch or something unrelated, and the store stayed broken until a
    human happened to look.

    🔴 THE RECOVERY COMMAND IS PER AFFECTED SCOPE, AND THAT IS THE WHOLE POINT OF
    ENUMERATING. The obvious version — "run `--validate` on your scope" — is
    UNFOLLOWABLE exactly when it matters most: a malformed entry in ANOTHER scope
    still aborts this repo's load (the loader reads the whole store), so that
    command would report the caller's own scope clean while the probe kept
    failing. That is the same shape as the instruction this change exists to fix.
    So the enumeration is re-read with `COLLECT` and one command is emitted for
    each scope that actually holds a reject, this repo's first.

    Fail-closed behaviour and the exit code are UNCHANGED: it still raises, and
    `main()` still exits 3. Only the words moved.

    ⚠ The enumeration is best-effort by construction. If the second read fails
    for any reason the ORIGINAL diagnosis is still returned in full — a message
    that turned one failure into a different one would be worse than a terse one.
    """
    store = Path(store_root)
    head = (
        f"{exc}\n"
        f"  REFUSED, NOT READ: the probe indexed NOTHING and proposed NOTHING. This is not "
        f"'nothing touched an entry' — no association was computed at all, because a writer "
        f"must not act on a store it has read only part of."
    )
    try:
        malformed = load_index(store, on_malformed=ON_MALFORMED_COLLECT).malformed
    except Exception:  # noqa: BLE001 — see the docstring: never mask the real diagnosis
        return head
    if not malformed:
        # The first read raised and the second found nothing: the store changed
        # under us, or the rejection is not reproducible. Say so rather than
        # printing an empty list under a heading that promises one.
        return (
            f"{head}\n"
            f"  (Re-reading {store} to enumerate every unreadable entry found NONE — the "
            f"store changed between the two reads. Re-run the probe.)"
        )

    here = normalize_ref(scope)
    mine = [m for m in malformed if m.scope == here]
    theirs = [m for m in malformed if m.scope != here]
    n = len(malformed)
    lines = [
        head,
        f"  {n} entry file{'' if n == 1 else 's'} under {store} cannot be indexed, and EVERY "
        f"reader skips {'it' if n == 1 else 'them'}:",
    ]
    lines += [f"    {m.line}" for m in malformed]
    if mine:
        lines.append(
            f"  {len(mine)} of {'them' if n > 1 else 'those'} "
            f"{'is' if len(mine) == 1 else 'are'} in THIS repo's scope `{here}/` — "
            f"{'it' if len(mine) == 1 else 'any of them'} may be the very entry your paths "
            f"would have matched, so do not conclude this session touched nothing."
        )
    if theirs:
        lines.append(
            f"  {len(theirs)} {'is' if len(theirs) == 1 else 'are'} in ANOTHER scope and "
            f"cannot affect this repo's association at all — but the loader reads the WHOLE "
            f"store, so {'it still blocks' if len(theirs) == 1 else 'they still block'} "
            f"this probe. "
            f"Running --validate on `{here}/` alone would report it clean and change nothing."
        )
    # This repo's scope first: it is the one the caller can act on without leaving
    # the task, and the ordering is deterministic so two runs print one diff.
    affected = [here] if mine else []
    affected += sorted({m.scope for m in theirs})
    lines.append("  RECOVER — fix the file(s) named above, then re-run this probe. Check with:")
    lines += [f"    {validate_command(store, s)}" for s in affected]
    return "\n".join(lines)


def render_validation(report: ValidationReport) -> str:
    """The validator's brief. Deterministic, and it states its own denominator."""
    out = [
        f"subsystem-touch validate: {report.target}",
        f"  store: {report.store_root}",
        f"  policy: {report.policy_path or '(none)'}  ({report.policy_basis})",
    ]
    if not report.checked:
        # 🔴 ITS OWN BRANCH AND ITS OWN WORDS. "0 malformed" over 0 files is the
        # reassuring zero from an instrument wired to nothing; it must not render
        # anywhere near the clean verdict.
        out.append("")
        out.append(
            f"NOTHING WAS CHECKED — no entry files were found for {report.target}. A zero "
            f"here is NOT a clean bill of health: it says the validator walked an empty or "
            f"absent directory, not that the entries parse."
        )
        return "\n".join(out)

    out.append(f"  checked: {len(report.checked)} entry file(s) — {', '.join(report.checked)}")
    if report.scope is None:
        out.append(
            "  scope: NOT checked for duplicate refs — that is a relationship between two "
            "files, and this run looked at one. Re-run as `--validate` (no path) for the "
            "whole scope."
        )
    out.append("")
    if report.clean:
        out.append(
            f"OK — {len(report.checked)} of {len(report.checked)} entry file(s) parse, "
            f"0 malformed. They will load, be listed, and be reachable by `--ref`."
        )
        return "\n".join(out)
    n = len(report.malformed)
    out.append(
        f"🔴 MALFORMED — {n} of {len(report.checked)} entry file(s) could NOT be indexed. "
        f"Every reader skips {'it' if n == 1 else 'them'}, so the content is invisible "
        f"until fixed:"
    )
    for m in report.malformed:
        out.append(f"  {m.line}")
    out.append(
        "  (Front matter is parsed LINE BY LINE. The commonest cause is a value wrapped "
        "across two physical lines — an `aliases: [...]` list in particular must be on ONE "
        "line, or the parser reads an unterminated bare string.)"
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
            "fallback source when no --session/--transcript/--pr/--commit is given: "
            "`git` (default) or `-` to read repo-relative paths from stdin, one per line"
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
    session_src.add_argument(
        "--commit",
        action="append",
        default=None,
        metavar="SHA[,SHA...]",
        help=(
            "report what these COMMITS changed. The workflow-agnostic source: a PR "
            "is a set of commits, worktree-authored work becomes a mainline commit, and "
            "a direct push IS a commit — so this reaches repos where --session sees "
            "only paths outside the cwd and --pr sees under a third of what lands. "
            "Repeatable and comma-separated. Hex shas only (4-40 chars); a revision "
            "expression, an ambiguous prefix and a merge commit are each refused by "
            "name."
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
        help="count entries by scope and created_by, PLUS write activity (newest write, entries touched in 24h/7d), and exit. The counts are the coverage instrument; the activity lines are the stall detector — the counts cannot detect a stall.",
    )
    p.add_argument(
        "--template",
        metavar="SLUG",
        default=None,
        help="print the minimal new-entry template for SLUG and exit",
    )
    p.add_argument(
        "--validate",
        nargs="?",
        const=VALIDATE_SCOPE,
        default=None,
        metavar="PATH",
        help=(
            "check that entry files PARSE, and exit. With a PATH, check that one file; "
            "with no argument, check every entry in this repo's scope (which also catches "
            "duplicate refs). Exit 3 if anything is malformed. It reuses the reader's own "
            "parser and validator, so a pass here is the reader's verdict and not a second "
            "opinion — run it right after writing an entry, because the tool that would "
            "otherwise tell you is a different tool in a later session."
        ),
    )
    return p


# The `--validate` sentinel for "no path given, do the whole scope". A const
# rather than `True` so the value is still a string everywhere downstream and one
# `is`-vs-`==` slip cannot turn "validate the scope" into a path named `True`.
VALIDATE_SCOPE = ""

# 🔴 ONE RULE, ONE PLACE — the three DO-THIS-AND-EXIT modes. Each pair of them is
# the same conflict, so three pairwise `if`s would be the predicate open-coded at
# three sites, wrong at two the first time a fourth mode arrives. They select
# different things and every combination has an obvious "sensible" reading that
# differs from the others', so the combination is refused rather than ordered.
_EXIT_MODES: tuple[tuple[str, str], ...] = (
    ("--census", "count entries by scope and writer, plus write activity"),
    ("--template", "print a new-entry template"),
    ("--validate", "check that entry files parse"),
)


def _comma_tokens(groups: Iterable[object]) -> list[str]:
    """`["1,2", "3"]` -> `["1", "2", "3"]`. ONE splitter for `--pr` and `--commit`.

    Empty tokens are DROPPED, which is a decision and not tidiness: it makes
    `--pr 421,` the typo it looks like rather than a second unusable number, and
    it makes `--pr ,` / `--commit ,` reach the source's "nothing was given" guard
    instead of its "unusable token" guard. Those are different facts, and each
    has its own test. A second copy of this would drift from that decision — the
    duplicated-predicate shape `claude/RULES.md` names.
    """
    return [tok for group in groups for tok in str(group).split(",") if tok.strip()]


def main(argv: Sequence[str] | None = None, *, today: str | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    from datetime import date  # local: the pure layer above never imports a clock

    stamp = args.today or today or date.today().isoformat()

    chosen = [
        flag
        for flag, _what in _EXIT_MODES
        if (flag == "--census" and args.census)
        or (flag == "--template" and args.template is not None)
        or (flag == "--validate" and args.validate is not None)
    ]
    if len(chosen) > 1:
        what = {f: w for f, w in _EXIT_MODES}
        print(
            "subsystem-touch: "
            + " and ".join(chosen)
            + " select different things ("
            + " vs ".join(what[f] for f in chosen)
            + "). Pass one.",
            file=sys.stderr,
        )
        return 2

    try:
        if args.census:
            # One clock read for the whole report: census() and render_census()
            # must not disagree about "now" in the same output.
            at = time.time()
            c = census(args.store, now=at)
            print(json.dumps(c.to_json(), indent=2) if args.json
                  else render_census(c, now=at))
            return 0

        repo = Path(args.repo).resolve()
        scope = args.scope if args.scope is not None else scope_for_repo(repo)

        if args.template is not None:
            print(new_entry_template(normalize_ref(args.template), scope, today=stamp))
            return 0

        if args.validate is not None:
            if args.validate == VALIDATE_SCOPE:
                checked, malformed = validate_scope(args.store, scope)
                policy_scope: str | None = scope
                target = f"`{normalize_ref(scope)}/`"
            else:
                bad = validate_entry_file(args.validate)
                checked = (Path(args.validate).name,)
                malformed = (bad,) if bad is not None else ()
                policy_scope = Path(args.validate).parent.name
                target = str(args.validate)
            path, basis = governing_policy(args.store, policy_scope or "")
            report = ValidationReport(
                store_root=str(args.store),
                target=target,
                scope=scope if args.validate == VALIDATE_SCOPE else None,
                checked=tuple(checked),
                malformed=tuple(malformed),
                policy_path=path,
                policy_basis=basis,
            )
            print(
                json.dumps(report.to_json(), indent=2)
                if args.json
                else render_validation(report)
            )
            # 🔴 3, the same code every other "the store is broken" condition
            # uses here. `handoff/SKILL.md` already says any non-zero exit means
            # print the line and write NOTHING, which is exactly the right
            # response to an entry that does not parse; a fourth exit code would
            # need every consumer to learn it before it changed any behaviour.
            return 0 if report.clean else 3

        wants_session = args.session is not None or args.transcript is not None
        wants_pr = args.pr is not None
        wants_commit = args.commit is not None
        # 🔴 A CONTRADICTION IS REFUSED, NOT RESOLVED. `--paths-from` names the
        # FALLBACK source, so pairing it with a session, PR or commit request
        # asks for two different windows at once. Honouring either silently would
        # hand back a plausible answer to a question the caller did not ask — the
        # same failure the no-fallback rule above exists to prevent, arriving
        # through argument parsing instead. (`--session` vs `--transcript` vs
        # `--pr` vs `--commit` is enforced one level up, by argparse's exclusive
        # group.)
        if (wants_session or wants_pr or wants_commit) and args.paths_from != "git":
            print(
                "subsystem-touch: --session/--transcript/--pr/--commit cannot be "
                "combined with --paths-from; they are different path windows. Drop one.",
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
            source = collect_pr_paths(repo, _comma_tokens(args.pr), exclude=args.exclude)
        elif wants_commit:
            # Same CLI spelling, same reasoning, and the SAME splitter — a second
            # copy would drift, and this one already encodes the decision that an
            # empty token reaches the "nothing was given" guard rather than the
            # "unusable token" one.
            source = collect_commit_paths(
                repo, _comma_tokens(args.commit), exclude=args.exclude
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
