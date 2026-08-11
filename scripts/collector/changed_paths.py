#!/usr/bin/env python3
"""changed_paths — the ONE definition of the `changed_paths*` block that both
session summarisers put on `kind=session-summary`.

WHY THIS IS A SHARED MODULE AND NOT COPIED INTO EACH TAILER
-----------------------------------------------------------
`claude/session-tailer.py` and `opencode/session_tailer.py` compute the same
rollup from two completely different stores, and they have already drifted once
in exactly the way `claude/RULES.md` -> "One rule, one place" predicts: the
opencode summariser's file/git extraction read key names the store never used,
so it emitted `files_modified=0` for every session that ever ran, for months,
while its own unit tests were green against fixtures built in the same wrong
shape. A predicate open-coded at two sites is typically wrong at one of them.
So the relativization, the dedupe, the ordering and the cap live here, once, and
each tailer supplies only the raw paths + the session cwd.

The consumer is `scripts/lib/subsystem_resolver.py` (`associate_paths`), which
takes REPO-RELATIVE paths and rejects an absolute path or a `..` traversal
outright — an absolute path drags `home`, `zach`, `workspace` into the component
set and manufactures associations. So this module's contract is exactly that
module's input contract: every path it emits is repo-relative and `..`-free, and
anything that could not be made so is COUNTED, never silently dropped.

THE TWO ZEROS THIS EXISTS TO KEEP APART
---------------------------------------
A short or empty `changed_paths` list can mean three different things, and a
consumer that cannot tell them apart will produce a confidently wrong answer:

    changed_paths == None    we could not observe the file set at all
                             (unreadable transcript / unreadable store). NOT a
                             claim that nothing was touched.
    changed_paths == []      we looked and this session modified no files.
    changed_paths == [...]   the paths, capped at `changed_paths_cap`;
                             `changed_paths_truncated` says whether the list is
                             the whole story and `changed_paths_total` is the
                             true distinct count before capping.

This mirrors `subsystem_resolver.Association.looked_at_nothing` /
`considered_paths` one layer upstream: the discriminator is a FIELD, not a
convention, because nothing can force a consumer to consult a convention.

THE CAP, AND HOW IT WAS CHOSEN
------------------------------
MEASURED 2026-08-11 against the live `activity.events` store (read-only), over
every `kind=session-summary` row ever emitted, deduped per session with
`argMax(files_modified, ingested_at)`:

    sessions   1104
    mean        6.7      p50   1      p90   20
    p99        53        p999  88     max   93
    sessions over 100 distinct files: 0      over 200: 0      over 256: 0

and the same read over the opencode SQLite store gave a max of 46 distinct
`filePath`s per session. So the cap is a BACKSTOP against a pathological
session, not a routine truncation: at 256 it is ~2.75x the largest session ever
observed and truncates 0 of the 1104 in the corpus. Payload cost is bounded
too — today's rollup payloads measure avg 724 / max 1328 bytes, and 256 paths at
a typical repo-relative length adds at most ~15 KB in the worst case that has
never happened.

Raise it only with a new measurement quoted. Lowering it below the measured p999
(88) would start truncating real sessions.

WHAT `changed_paths_outside_cwd` ACTUALLY COUNTS — MEASURED, NOT ASSUMED
------------------------------------------------------------------------
A dry run over 700 real Claude transcripts (2026-08-11, read-only, nothing
emitted) found 3,384 distinct modified paths, of which 525 (16%) were under the
session cwd and 2,859 (84%) were not. Classified by prefix, the excluded set is
dominated by:

    ~1,280  the per-session agent scratchpad under /tmp
      ~700  elsewhere under the user's home (other repos, ~/.claude, dotfiles)
     ~150+  short-lived git worktrees under /tmp, spread over ~30 directories

The first two are exactly what MUST be excluded — a scratchpad file or a file in
a different repo has no repo-relative form here, and inventing one would hand
the resolver components (`tmp`, `home`, a foreign repo's name) that manufacture
associations. The third is a genuine loss: a temp worktree IS repo content, but
nothing in a transcript maps a worktree back to its repo, and the resolver's
design forbids persisting a location to find out. So it is COUNTED rather than
guessed at, and a consumer comparing `changed_paths_total` against
`changed_paths_outside_cwd` can see how much of a session it is not being told
about. `total + outside_cwd == files_modified` holds by construction and is
pinned by a test.

⚠ TRUNCATION BIAS, STATED RATHER THAN LEFT TO BE DISCOVERED: the list is sorted
lexicographically, so a truncated list is a lexicographic PREFIX and can
therefore omit an entire late-sorting subtree. Sorting is what makes two emits
of one session byte-identical regardless of the order the store was walked in
(this event kind is append-only and re-emitted; see the tailers' settle policy),
which is worth more than the bias in a branch the corpus says never fires — but
a consumer that ignores `changed_paths_truncated` and reads a truncated list as
complete will under-associate, which is why the flag is a peer field and not a
comment.
"""
from __future__ import annotations

import posixpath
from typing import Iterable

__all__ = [
    "CHANGED_PATHS_CAP",
    "PAYLOAD_KEYS",
    "to_repo_relative",
    "summarize",
    "unobservable",
]

# See the module docstring for the measurement behind this number.
CHANGED_PATHS_CAP = 256

# The exact key set this module owns on the payload. Both tailers place all of
# these, always — a key that is sometimes absent is indistinguishable from a
# consumer reading the wrong name.
PAYLOAD_KEYS = (
    "changed_paths",
    "changed_paths_total",
    "changed_paths_truncated",
    "changed_paths_outside_cwd",
    "changed_paths_cap",
)


def _normalize_dir(cwd: str) -> str:
    """Lexically normalize a session cwd for prefix comparison.

    Purely lexical (`posixpath.normpath`) — deliberately NOT `realpath`. The
    tailers run over historical sessions whose cwd may no longer exist, and
    resolving symlinks would make the emitted paths depend on the filesystem at
    summarisation time rather than on the transcript.
    """
    if not cwd:
        return ""
    return posixpath.normpath(cwd).rstrip("/")


def to_repo_relative(path: str, cwd: str) -> str | None:
    """One changed path -> its repo-relative form, or None if it has none.

    None means "this path cannot be expressed relative to the session cwd", and
    the caller COUNTS those (`changed_paths_outside_cwd`) rather than dropping
    them — a session in one repo that edits a file in another is real, and a
    silently short list is the failure mode this whole module exists to prevent.

    Rules, in order:
      * a relative path is kept as-is once normalized, provided it does not
        escape (`..`) — the resolver rejects `..` and normalizing it away would
        turn a caller bug into a plausible-looking association;
      * an absolute path is kept iff it is strictly UNDER `cwd`, with the `cwd`
        prefix stripped;
      * an absolute path with an empty `cwd`, or outside it, has no
        repo-relative form -> None.

    A path equal to the cwd itself is None: a directory is not a changed file,
    and "" is not a path the resolver can use.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError(
            f"changed-paths entry is not a usable path: {path!r} "
            "(expected a non-empty string)"
        )
    if not isinstance(cwd, str):
        raise ValueError(
            f"changed-paths cwd must be a string, got {type(cwd).__name__} "
            f"({cwd!r}); pass '' when the session has no cwd"
        )

    norm = posixpath.normpath(path.strip())
    if not norm.startswith("/"):
        # Already relative. `normpath` has collapsed interior `..`; anything
        # LEADING still escapes the root, so it is not repo-relative.
        if norm == "." or norm.startswith(".."):
            return None
        return norm

    base = _normalize_dir(cwd)
    if not base or not base.startswith("/"):
        return None
    # The separator in `base + "/"` is load-bearing twice over, and a mutation
    # sweep is what established it:
    #   * it stops a SIBLING directory sharing a name prefix from being
    #     relativized — `/x/repo-2/a.py` against cwd `/x/repo` would otherwise
    #     become `-2/a.py`, a path that matches nothing and lies about where it
    #     came from;
    #   * it is also what excludes the cwd ITSELF (a directory is not a changed
    #     file): `"/x/repo".startswith("/x/repo/")` is False. An explicit
    #     `if norm == base: return None` above this was therefore UNKILLABLE —
    #     no input could distinguish the code with it from the code without —
    #     so it was removed rather than left looking like a guard.
    prefix = base + "/"
    if not norm.startswith(prefix):
        return None
    rel = norm[len(prefix):]
    return rel or None


def unobservable(*, cap: int = CHANGED_PATHS_CAP) -> dict:
    """The block to emit when the file set could not be observed AT ALL.

    Every data field is None; only the cap — a constant of this code, not a
    reading of the session — stays populated, so a consumer can still see which
    bound the emitter was built with.
    """
    _check_cap(cap)
    return {
        "changed_paths": None,
        "changed_paths_total": None,
        "changed_paths_truncated": None,
        "changed_paths_outside_cwd": None,
        "changed_paths_cap": cap,
    }


def _check_cap(cap: int) -> None:
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise ValueError(
            f"changed-paths cap must be an int >= 1, got {cap!r} — a cap of 0 "
            "would emit an empty list that reads as 'no files were touched'"
        )


def summarize(paths: Iterable[str], cwd: str, *, cap: int = CHANGED_PATHS_CAP) -> dict:
    """Build the `changed_paths*` block from raw changed paths + the session cwd.

    `paths` may repeat and may arrive in any order; the result is deduplicated
    on the REPO-RELATIVE form and sorted, so it is a canonical form of the input
    set. `changed_paths_total` is the distinct count BEFORE the cap, which is
    what makes a truncated list readable as truncated even by a consumer that
    only looks at the numbers.

    Guards (each with its own sentinel phrase, each reachable by an input no
    earlier guard rejects):
        "changed-paths cap must be an int >= 1"     cap=0
        "changed-paths cwd must be a string"        cwd=None
        "changed-paths entry is not a usable path"  paths=[None] / ['']
    """
    _check_cap(cap)
    if not isinstance(cwd, str):
        raise ValueError(
            f"changed-paths cwd must be a string, got {type(cwd).__name__} "
            f"({cwd!r}); pass '' when the session has no cwd"
        )

    rel: set[str] = set()
    outside: set[str] = set()
    for raw in paths:
        r = to_repo_relative(raw, cwd)
        if r is None:
            # `raw` is known to be a non-empty str here: `to_repo_relative`
            # raises on anything else rather than returning None, so this branch
            # never sees one and does not defend against it.
            outside.add(raw)
        else:
            rel.add(r)

    ordered = sorted(rel)
    total = len(ordered)
    return {
        "changed_paths": ordered[:cap],
        "changed_paths_total": total,
        "changed_paths_truncated": total > cap,
        "changed_paths_outside_cwd": len(outside),
        "changed_paths_cap": cap,
    }
