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
A dry run over every transcript this tailer walks — 600 of them, i.e. after
`iter_transcripts` drops the `subagents/` and `wf_*` dirs — found (2026-08-11,
read-only, nothing emitted) 3,290 distinct modified paths, of which 470 (14.3%)
were under the session cwd and 2,820 (85.7%) were not. Classified by prefix, the
excluded set breaks down as:

    1,225  the per-session agent scratchpad under /tmp
      802  short-lived git worktrees under /tmp — across 487 distinct `wt-*`
           worktree roots (645 distinct leaf directories)
      626  elsewhere under the user's home (other repos, ~/.claude, dotfiles)
      167  other /tmp paths

The scratchpad and other-repo buckets are exactly what MUST be excluded — a
scratchpad file or a file in a different repo has no repo-relative form here, and
inventing one would hand the resolver components (`tmp`, `home`, a foreign repo's
name) that manufacture associations. The worktree bucket is a genuine loss: a
temp worktree IS repo content, but nothing in a transcript maps a worktree back
to its repo, and the resolver's design forbids persisting a location to find out.
So it is COUNTED rather than guessed at, and a consumer comparing
`changed_paths_total` against `changed_paths_outside_cwd` can see how much of a
session it is not being told about. `total + outside_cwd == files_modified` holds
by construction and is pinned by a test.

🔴 CORRECTED 2026-08-11 (same day, before the numbers could be quoted anywhere
else): the worktree bucket previously read "~150+ … spread over ~30 directories".
Re-measured it is **802 paths over 487 worktree roots** — understated ~5.3x on
paths and ~16x on directories. The conclusion is unchanged, and the corrected
figure is what makes it worth stating: the counted-but-dropped worktree set alone
is **1.7x larger than the entire emitted path set** (802 vs 470), so a consumer
that reads `changed_paths` as the session's file list, rather than as a subset
sized by `changed_paths_outside_cwd`, is wrong by more than the data it has.

Every count above is a measurement, not an estimate: the four buckets sum
exactly to the 2,820 excluded, and the in/out headline reproduces across three
independent runs (14.3% / 15.2% / 16%). What the superseded figures lacked was a
measurement of the SUB-classification — the split was approximated while the
headline was counted, which is why only it was wrong.

🔴 ADDED 2026-08-13 — A SECOND FRAME, `absolute_under`, WHICH RECOVERS PART OF
THAT LOSS. Everything above describes ONE frame: the session's own cwd. It is
the only frame a RELATIVE entry can be read in, but not the only one an ABSOLUTE
entry can — a transcript records the tool call's `file_path` verbatim, so an
entry the caller spelled out in full carries its own frame and is attributable
to whatever tree it names. `summarize` still counts every such entry as
`outside_cwd`, unchanged; `absolute_under(paths, root)` answers the separate
question "which of these name files under THIS root", and is the only way the
other-repo bucket above becomes readable rather than merely counted. Its keys
are `ABSOLUTE_KEYS`, deliberately NOT part of `PAYLOAD_KEYS` — see there.

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
    "ABSOLUTE_KEYS",
    "to_repo_relative",
    "summarize",
    "unobservable",
    "absolute_under",
    "absolute_unobservable",
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

# The ABSOLUTE-window keys — see `absolute_under`. 🔴 DELIBERATELY NOT IN
# `PAYLOAD_KEYS`: this block is computed only when a caller names a root, and the
# tailers' emit path never does. It is an on-demand answer for a local reader,
# not a field of the telemetry event, and adding it to the payload would ship a
# second path list to ClickHouse for every session.
ABSOLUTE_KEYS = (
    "changed_paths_absolute",
    "changed_paths_absolute_total",
    "changed_paths_absolute_truncated",
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


def absolute_unobservable() -> dict:
    """The absolute block to emit when the file set could not be observed AT ALL.

    The peer of `unobservable()`, and it exists for the same reason: an empty
    LIST here would read as "the root was checked and nothing resolved under it",
    which is a measurement. None says "we never got a file set to check".
    """
    return {
        "changed_paths_absolute": None,
        "changed_paths_absolute_total": None,
        "changed_paths_absolute_truncated": None,
    }


def absolute_under(paths: Iterable[str], root: str, *, cap: int = CHANGED_PATHS_CAP) -> dict:
    """The paths that are ABSOLUTE and resolve under `root`, made root-relative.

    🔴 WHAT THIS IS FOR, AND WHY IT IS NOT `summarize(paths, root)`. A transcript
    records the tool call's `file_path` verbatim: ABSOLUTE when the caller passed
    an absolute path, RELATIVE when it passed a relative one. `summarize` reads
    both against ONE frame — the session's own cwd — which is correct for the
    session's own repo and catastrophic for any other: re-anchoring a RELATIVE
    path against a different root manufactures an association out of nothing,
    since `src/a.py` in session-cwd A and `src/a.py` under repo B are unrelated
    strings that happen to spell the same thing.

    So this function reports ONLY what needs no inference at all — an entry that
    is `posixpath.isabs()` and lexically under `root`. A relative entry is
    excluded UNCONDITIONALLY, not because it is unlikely to belong to `root` but
    because nothing in the transcript says whether it does. The result is
    therefore safe to attribute to `root` no matter whose session produced it,
    which is what makes a cross-repo session readable at all.

    Prefix matching is `to_repo_relative`'s, reused rather than restated, so the
    sibling-directory trap it documents (`/x/repo-2/a.py` against `/x/repo`)
    cannot be reintroduced here. Matching is LEXICAL for that function's stated
    reason — a `realpath` pass would make the answer depend on the filesystem at
    read time — which under-reports through a symlinked root and never
    over-reports. `root` must be absolute; anything else yields nothing.

    Deduplicated on the root-relative form, sorted, and capped exactly as
    `summarize` is, with `..._total` carrying the pre-cap count so a truncated
    list is readable as truncated from the numbers alone.
    """
    _check_cap(cap)
    if not isinstance(root, str):
        raise ValueError(
            f"changed-paths root must be a string, got {type(root).__name__} "
            f"({root!r}); pass '' when there is no root to compare against"
        )

    rel: set[str] = set()
    # The input is validated whether or not the root is usable: a malformed entry
    # is a defect in the transcript reader, and hiding it behind an unrelated
    # argument would make the same corpus raise or not depending on the caller.
    #
    # ⚠ TWO LINES THAT LOOKED LIKE GUARDS WERE REMOVED FROM HERE AS UNKILLABLE —
    # a local `_normalize_dir(root)`, and an `if not base.startswith("/")` skip.
    # A mutation sweep could not distinguish the code with them from the code
    # without: `to_repo_relative` normalizes its own `cwd`, and already returns
    # None for EVERY path when that `cwd` is empty or relative. Same reasoning
    # and same outcome as the `if norm == base` guard removed from that function.
    # A redundant line that LOOKS like a guard invites a maintainer to trust it
    # and a sweep to report a false survivor; the behaviour it appeared to
    # provide is pinned by a test instead
    # (`test_a_root_that_is_not_absolute_yields_nothing_rather_than_guessing`).
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                f"changed-paths entry is not a usable path: {raw!r} "
                "(expected a non-empty string)"
            )
        # The absolute test is the whole safety property: `to_repo_relative`
        # returns a relative entry unchanged whatever the frame, so without this
        # line every relative path in the transcript would be reported as if it
        # belonged to `root`.
        if not posixpath.isabs(raw.strip()):
            continue
        r = to_repo_relative(raw, root)
        if r is not None:
            rel.add(r)

    ordered = sorted(rel)
    total = len(ordered)
    return {
        "changed_paths_absolute": ordered[:cap],
        "changed_paths_absolute_total": total,
        "changed_paths_absolute_truncated": total > cap,
    }
